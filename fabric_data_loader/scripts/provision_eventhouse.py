"""
Provision Fabric Eventhouse — create tables and ingest CSV telemetry data.

Module role:
    Automates the full lifecycle of a Microsoft Fabric Eventhouse for network
    telemetry storage. This is step 2 (after provision_lakehouse.py) in the
    Fabric infrastructure provisioning pipeline. The Eventhouse provides the
    KQL (Kusto Query Language) query engine that powers real-time telemetry
    analysis and anomaly detection in the graph demo.

    Orchestration steps:
      1. Create (or recreate) an Eventhouse via the Fabric REST API.
      2. Discover the auto-created default KQL database and its query URI.
      3. Derive KQL table schemas from scenario.yaml container definitions
         and CSV header rows, then create the tables.
      4. Ingest CSV telemetry data via queued ingestion (azure-kusto-ingest),
         falling back to inline streaming ingestion if queued is unavailable.
      5. Verify row counts and persist connection details to azure_config.env.

Key collaborators:
    - _config.py: Supplies workspace/capacity IDs, API base URL, and project
      root path loaded from azure_config.env.
    - provision_lakehouse.py: Must run first to create the Fabric workspace.
      This module depends on FABRIC_WORKSPACE_ID being populated.
    - scenario.yaml: Defines telemetry container names, CSV file mappings,
      and numeric_fields lists used to build KQL table schemas.
    - azure-kusto-data / azure-kusto-ingest SDKs: Execute KQL management
      commands and perform queued/inline CSV ingestion.
    - Fabric REST API (api.fabric.microsoft.com): Manages Eventhouse and
      KQL database lifecycle via long-running operations.

Dependents:
    - graph_demo_v2 backend agents consume the KQL tables for telemetry
      queries and anomaly detection.
    - azure_config.env consumers (deploy scripts, app runtime) rely on
      EVENTHOUSE_QUERY_URI and FABRIC_KQL_DB_NAME written by this module.

Prerequisites:
    - provision_lakehouse.py has run (workspace exists).
    - azure_config.env populated with FABRIC_WORKSPACE_ID, FABRIC_CAPACITY_ID.
    - Data files exist in data/scenarios/<name>/data/telemetry/.

Usage:
    uv run provision_eventhouse.py
"""

import csv
import os
import re
import sys
import time

import requests
import yaml
from azure.identity import DefaultAzureCredential
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.ingest import QueuedIngestClient, IngestionProperties
from azure.kusto.data.data_format import DataFormat  # Enum for ingestion format hints

# _config centralizes env-var loading from azure_config.env into typed constants
from _config import (
    FABRIC_API, PROJECT_ROOT,
    WORKSPACE_ID, WORKSPACE_NAME, CAPACITY_ID, EVENTHOUSE_NAME,
)

# ---------------------------------------------------------------------------
# Configuration — derived from scenario.yaml + CSV headers
# ---------------------------------------------------------------------------

# DEFAULT_SCENARIO selects which scenario directory under data/scenarios/
# contains the telemetry CSVs and scenario.yaml definition.
SCENARIO = os.environ.get("DEFAULT_SCENARIO", "")
if not SCENARIO:
    print("ERROR: DEFAULT_SCENARIO not set"); sys.exit(1)

# Absolute path to the telemetry CSV directory for the active scenario
DATA_DIR = str(PROJECT_ROOT / "data" / "scenarios" / SCENARIO / "data" / "telemetry")

# scenario.yaml is the single source of truth for table names, CSV file
# mappings, and numeric field declarations used to construct KQL schemas.
_SCENARIO_YAML = PROJECT_ROOT / "data" / "scenarios" / SCENARIO / "scenario.yaml"
if not _SCENARIO_YAML.exists():
    print(f"ERROR: scenario.yaml not found: {_SCENARIO_YAML}"); sys.exit(1)

with open(_SCENARIO_YAML) as _f:
    _SCENARIO_CFG = yaml.safe_load(_f)

# Navigate into data_sources.telemetry.config.containers — the list of
# container dicts, each specifying a KQL table name, optional csv_file
# override, and numeric_fields for type inference.
_TELEMETRY_CFG = _SCENARIO_CFG.get("data_sources", {}).get("telemetry", {}).get("config", {})
_CONTAINERS = _TELEMETRY_CFG.get("containers", [])


def _build_table_schemas() -> dict[str, dict[str, str]]:
    """Derive KQL table schemas from scenario.yaml container definitions and CSV headers.

    Purpose:
        Constructs an ordered mapping of table_name -> {column_name: kql_type}
        by combining two sources: the container metadata in scenario.yaml
        (which declares numeric_fields) and the actual CSV header row (which
        provides column names and ordering). This dual-source approach avoids
        hardcoding schemas while allowing type overrides for numeric columns.

    Parameters:
        None. Reads module-level _CONTAINERS (from scenario.yaml) and DATA_DIR.

    Returns:
        dict[str, dict[str, str]]: Mapping of table names to ordered dicts of
        {column_name: kql_type}. KQL types are 'datetime' (for Timestamp),
        'real' (for declared numeric fields), or 'string' (default).

    Side effects:
        Prints warnings to stdout for missing or empty CSV files. These tables
        are silently skipped (not fatal) to allow partial provisioning.

    Raises:
        No exceptions raised; errors are handled via warnings and skipping.

    Dependencies:
        _CONTAINERS (module-level list parsed from scenario.yaml),
        DATA_DIR (path to telemetry CSV directory).

    Dependents:
        TABLE_SCHEMAS module constant, create_kql_tables(), ingest_csv_files().
    """
    schemas: dict[str, dict[str, str]] = {}
    for container in _CONTAINERS:
        table_name = container["name"]
        # Allow scenario.yaml to override the default <table_name>.csv convention
        csv_file = container.get("csv_file", f"{table_name}.csv")
        csv_path = os.path.join(DATA_DIR, csv_file)
        # Pre-compute set for O(1) membership checks during column iteration
        numeric = set(container.get("numeric_fields", []))

        if not os.path.exists(csv_path):
            print(f"WARNING: CSV not found for table {table_name}: {csv_path}")
            continue

        # Read only the header row — data rows are not needed for schema derivation
        with open(csv_path) as f:
            reader = csv.reader(f)
            header = next(reader, None)

        if not header:
            print(f"WARNING: Empty CSV for table {table_name}: {csv_path}")
            continue

        # Map each CSV column to a KQL type using a three-tier priority:
        # 1. "Timestamp" column name -> datetime (convention across all telemetry CSVs)
        # 2. Membership in numeric_fields -> real (floating-point for metrics)
        # 3. Fallback -> string (safe default; KQL handles implicit conversion)
        col_types: dict[str, str] = {}
        for col in header:
            col = col.strip()
            if col == "Timestamp":
                col_types[col] = "datetime"
            elif col in numeric:
                col_types[col] = "real"
            else:
                col_types[col] = "string"

        schemas[table_name] = col_types
    return schemas


# TABLE_SCHEMAS is the authoritative mapping of KQL table names to their
# column schemas, constructed at import time from scenario.yaml + CSV headers.
# Every downstream function (create_kql_tables, ingest_csv_files, main)
# iterates over this dict to determine which tables to create and ingest.
# If empty, the scenario has no valid telemetry containers — fatal error.
TABLE_SCHEMAS = _build_table_schemas()
if not TABLE_SCHEMAS:
    print("ERROR: No telemetry tables found in scenario.yaml containers"); sys.exit(1)


# ---------------------------------------------------------------------------
# Fabric REST API client (subset needed for Eventhouse)
# ---------------------------------------------------------------------------

class FabricClient:
    """REST client for Microsoft Fabric Eventhouse lifecycle operations.

    Purpose:
        Encapsulates authenticated HTTP calls to the Fabric REST API for
        creating, deleting, and discovering Eventhouses and KQL databases.
        Handles long-running operation (LRO) polling, transient error
        retries, and bearer token management.

    Role in system:
        Data-plane provisioner. Called exclusively by main() to stand up
        the Eventhouse infrastructure. Does not manage Lakehouse or other
        Fabric item types — those live in provision_lakehouse.py.

    Lifecycle:
        Instantiated once in main(). The DefaultAzureCredential instance
        is created at __init__ and reused for all token acquisitions during
        the provisioning run. No explicit teardown needed.

    Key collaborators:
        - _config.FABRIC_API: Base URL for all REST calls.
        - DefaultAzureCredential: Provides OAuth tokens for Fabric API.
    """

    def __init__(self):
        """Initialize with a shared DefaultAzureCredential for token acquisition."""
        self.credential = DefaultAzureCredential()

    def _get_token(self) -> str:
        """Acquire a bearer token scoped to the Fabric API.

        Purpose:
            Fetches a fresh OAuth2 token from DefaultAzureCredential. Called
            on every request via the headers property to ensure tokens are
            never stale (DefaultAzureCredential caches internally).

        Returns:
            str: Raw bearer token string.

        Raises:
            azure.core.exceptions.ClientAuthenticationError: If no valid
            credential chain is available (e.g., not logged in via az CLI).
        """
        return self.credential.get_token(
            "https://api.fabric.microsoft.com/.default"
        ).token

    @property
    def headers(self) -> dict:
        """Build HTTP headers with a fresh bearer token for each request.

        Returns:
            dict: Authorization and Content-Type headers for Fabric REST calls.
        """
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def _wait_for_lro(
        self, response: requests.Response, label: str, timeout: int = 300
    ):
        """Poll a Fabric long-running operation until completion or failure.

        Purpose:
            Fabric REST API returns 202 for asynchronous operations (e.g.,
            Eventhouse creation). This method polls the operation status
            endpoint until the operation succeeds, fails, or times out.

        Parameters:
            response (requests.Response): The initial HTTP response from
                the Fabric API call. Status 201/200 are treated as immediate
                success; 202 triggers LRO polling.
            label (str): Human-readable description of the operation, used
                in progress/error messages (e.g., "Create Eventhouse 'foo'").
            timeout (int): Maximum seconds to wait for LRO completion.
                Defaults to 300 (5 minutes). Eventhouse creation typically
                completes in 30-60 seconds.

        Returns:
            dict: Parsed JSON response body from the completed operation.
                For 202 responses, fetches the result from the /result
                sub-endpoint after the operation succeeds.

        Side effects:
            Prints failure messages and calls sys.exit(1) on non-recoverable
            errors (unexpected status codes, missing operation IDs, timeouts,
            or operation failure/cancellation).

        Raises:
            SystemExit: On any unrecoverable error condition.
        """
        # 201 Created — synchronous success, no polling needed
        if response.status_code == 201:
            return response.json()

        # Reject unexpected status codes before entering the poll loop
        if response.status_code not in (200, 202):
            print(f"  ✗ {label} failed: {response.status_code} — {response.text}")
            sys.exit(1)

        # 200 OK — synchronous success (some Fabric endpoints return 200)
        if response.status_code == 200:
            return response.json()

        # 202 Accepted — extract the operation ID for LRO polling
        operation_id = response.headers.get("x-ms-operation-id")
        if not operation_id:
            print(f"  ✗ {label}: no operation ID in 202 response")
            sys.exit(1)

        url = f"{FABRIC_API}/operations/{operation_id}"
        # Respect the server-suggested polling interval to avoid throttling
        retry_after = int(response.headers.get("Retry-After", "5"))

        elapsed = 0
        while elapsed < timeout:
            time.sleep(retry_after)
            elapsed += retry_after
            r = requests.get(url, headers=self.headers)
            if r.status_code != 200:
                # Transient failure on status poll — retry silently
                continue
            status = r.json().get("status", "")
            if status == "Succeeded":
                # Fetch the operation result from the /result sub-endpoint;
                # fall back to the status response if /result is unavailable
                result_url = f"{url}/result"
                rr = requests.get(result_url, headers=self.headers)
                return rr.json() if rr.status_code == 200 else r.json()
            if status in ("Failed", "Cancelled"):
                print(f"  ✗ {label} {status}: {r.json()}")
                sys.exit(1)

        print(f"  ✗ {label} timed out after {timeout}s")
        sys.exit(1)

    def find_eventhouse(self, workspace_id: str, name: str) -> dict | None:
        """Search for an existing Eventhouse by display name.

        Purpose:
            Checks whether an Eventhouse with the given name already exists
            in the workspace. Used by main() to decide whether to delete
            and recreate (idempotent provisioning).

        Parameters:
            workspace_id (str): Fabric workspace GUID.
            name (str): Display name to match (exact, case-sensitive).

        Returns:
            dict | None: The Eventhouse item dict if found, else None.

        Raises:
            requests.exceptions.HTTPError: On non-2xx status from the
                list eventhouses endpoint.
        """
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}/eventhouses",
            headers=self.headers,
        )
        r.raise_for_status()
        # Linear scan — workspace typically has very few Eventhouses
        for item in r.json().get("value", []):
            if item["displayName"] == name:
                return item
        return None

    def delete_eventhouse(self, workspace_id: str, eventhouse_id: str, name: str):
        """Delete an Eventhouse by its item ID.

        Purpose:
            Removes an existing Eventhouse to allow clean recreation.
            Called during idempotent provisioning when an Eventhouse with
            the target name already exists. Non-fatal on failure — the
            subsequent create_eventhouse call will surface the real error.

        Parameters:
            workspace_id (str): Fabric workspace GUID.
            eventhouse_id (str): Item GUID of the Eventhouse to delete.
            name (str): Display name, used only for log messages.

        Returns:
            None.

        Side effects:
            Prints success/warning messages. Does NOT call sys.exit on
            failure — deletion errors are treated as non-fatal because
            the name may still become available after a propagation delay.
        """
        r = requests.delete(
            f"{FABRIC_API}/workspaces/{workspace_id}/eventhouses/{eventhouse_id}",
            headers=self.headers,
        )
        if r.status_code in (200, 204):
            print(f"  ✓ Deleted existing Eventhouse: {name} ({eventhouse_id})")
        else:
            # Non-fatal: Fabric sometimes returns errors on delete but the
            # item is still removed after a propagation delay
            print(f"  ⚠ Delete Eventhouse failed: {r.status_code} — {r.text}")
            print(f"    Continuing anyway...")

    def create_eventhouse(self, workspace_id: str, name: str, max_retries: int = 10, retry_delay: int = 30) -> dict:
        """Create a new Eventhouse with retry logic for name availability.

        Purpose:
            Posts a create-eventhouse request to the Fabric REST API. After
            a previous deletion, the display name may remain reserved for
            a propagation window. This method retries on the specific
            "ItemDisplayNameNotAvailableYet" error code to handle that
            race condition transparently.

        Parameters:
            workspace_id (str): Fabric workspace GUID.
            name (str): Desired display name for the Eventhouse.
            max_retries (int): Maximum retry attempts for name-availability
                errors. Defaults to 10 (~5 min with default delay).
            retry_delay (int): Seconds between retry attempts. Defaults to
                30. Fabric name propagation typically resolves in 1-3 min.

        Returns:
            dict: Parsed JSON of the created Eventhouse item, including
            'id', 'displayName', and 'properties'.

        Side effects:
            Prints progress messages during retries. Delegates to
            _wait_for_lro for the final 202 -> success polling.

        Raises:
            SystemExit: If name remains unavailable after all retries,
                or if _wait_for_lro encounters a non-recoverable error.
        """
        body = {
            "displayName": name,
            "description": f"Eventhouse for {WORKSPACE_NAME}",
        }
        url = f"{FABRIC_API}/workspaces/{workspace_id}/eventhouses"

        for attempt in range(1, max_retries + 1):
            r = requests.post(url, headers=self.headers, json=body)

            # Handle the transient name-reservation conflict that occurs
            # after deleting an Eventhouse with the same display name
            if r.status_code == 400:
                try:
                    err = r.json()
                    error_code = err.get("errorCode", "")
                except Exception:
                    error_code = ""

                if error_code == "ItemDisplayNameNotAvailableYet":
                    print(f"  ⏳ Name not available yet (attempt {attempt}/{max_retries}), retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    continue

            # For any non-retryable response, delegate to LRO handler
            return self._wait_for_lro(r, f"Create Eventhouse '{name}'")

        print(f"  ✗ Eventhouse name '{name}' still not available after {max_retries} attempts ({max_retries * retry_delay}s)")
        sys.exit(1)

    def find_kql_database(
        self, workspace_id: str, eventhouse_id: str
    ) -> dict | None:
        """Locate the default KQL database auto-created with an Eventhouse.

        Purpose:
            Every Eventhouse creates a default KQL database on provisioning.
            This method finds it by matching parentEventhouseItemId in the
            database properties. The returned dict contains the query URI
            needed to connect via the Kusto SDK.

        Parameters:
            workspace_id (str): Fabric workspace GUID.
            eventhouse_id (str): Item GUID of the parent Eventhouse.

        Returns:
            dict | None: KQL database item dict (including 'displayName',
            'id', and 'properties.queryServiceUri') if found. Falls back
            to the first database in the workspace if parent matching fails
            (covers edge cases where properties are not yet populated).
            Returns None if no databases exist.

        Raises:
            requests.exceptions.HTTPError: On non-2xx status from the
                list kqlDatabases endpoint.
        """
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}/kqlDatabases",
            headers=self.headers,
        )
        r.raise_for_status()
        # Prefer exact match on parent Eventhouse ID
        for db in r.json().get("value", []):
            props = db.get("properties", {})
            if props.get("parentEventhouseItemId") == eventhouse_id:
                return db
        # Fallback: if parent match fails (properties still propagating),
        # return the first available KQL database in the workspace
        dbs = r.json().get("value", [])
        return dbs[0] if dbs else None


# ---------------------------------------------------------------------------
# KQL table creation via management commands
# ---------------------------------------------------------------------------

def create_kql_tables(kusto_client: KustoClient, db_name: str):
    """Create KQL tables and CSV ingestion mappings from TABLE_SCHEMAS.

    Purpose:
        Issues KQL management commands to create tables (idempotently via
        .create-merge) and register CSV ingestion column mappings so that
        the subsequent ingest_csv_files() call can reference them by name.

    Parameters:
        kusto_client (KustoClient): Authenticated Kusto client connected
            to the Eventhouse query URI.
        db_name (str): Name of the target KQL database.

    Returns:
        None.

    Side effects:
        - Creates or merges tables in the KQL database.
        - Creates or alters CSV ingestion mappings (one per table).
        - Prints per-table progress to stdout.
        - Calls sys.exit(1) on any management command failure.

    Raises:
        SystemExit: If any .create-merge or .create-or-alter command fails.

    Dependencies:
        TABLE_SCHEMAS (module constant), KustoClient.execute_mgmt.

    Dependents:
        Called by main() after Eventhouse and KQL database are provisioned.
    """
    # Phase 1: Create tables using .create-merge (idempotent — adds new
    # columns without dropping existing ones if table already exists)
    for table_name, schema in TABLE_SCHEMAS.items():
        columns = ", ".join(f"['{col}']: {dtype}" for col, dtype in schema.items())
        cmd = f".create-merge table {table_name} ({columns})"

        print(f"  Creating table: {table_name} ...", end=" ")
        try:
            kusto_client.execute_mgmt(db_name, cmd)
            print("✓")
        except Exception as e:
            print(f"✗ {e}")
            sys.exit(1)

    # Phase 2: Register CSV ingestion mappings. Each mapping tells the
    # ingestion engine how to map CSV column ordinals to table columns
    # and what data type conversion to apply.
    for table_name, schema in TABLE_SCHEMAS.items():
        cols = list(schema.keys())
        mapping_name = f"{table_name}_csv_mapping"
        # Build the JSON mapping array: each entry maps a column name,
        # data type, and ordinal position (0-based) in the CSV file
        mapping_json = ", ".join(
            f'{{"Name": "{col}", "DataType": "{dtype}", "Ordinal": {i}}}'
            for i, (col, dtype) in enumerate(schema.items())
        )
        cmd = (
            f'.create-or-alter table {table_name} ingestion csv mapping '
            f"'{mapping_name}' '[{mapping_json}]'"
        )
        print(f"  CSV mapping: {mapping_name} ...", end=" ")
        try:
            kusto_client.execute_mgmt(db_name, cmd)
            print("✓")
        except Exception as e:
            print(f"✗ {e}")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Data ingestion via Kusto SDK queued ingestion
# ---------------------------------------------------------------------------

def ingest_csv_files(
    query_uri: str,
    db_name: str,
):
    """Ingest CSV telemetry data into KQL tables via queued ingestion.

    Purpose:
        Submits each telemetry CSV file to the Kusto queued ingestion
        service, which processes files asynchronously in the background.
        If queued ingestion is unavailable (some Fabric SKUs restrict it),
        falls back to _streaming_ingest_fallback() which uses .ingest
        inline management commands.

    Parameters:
        query_uri (str): The Eventhouse KQL query endpoint URI
            (e.g., https://<id>.z<n>.kusto.fabric.microsoft.com).
            Converted to the ingest endpoint by prefixing 'ingest-'.
        db_name (str): Name of the target KQL database.

    Returns:
        None.

    Side effects:
        - Submits CSV files to the Kusto ingestion queue (async processing).
        - On queued ingestion failure, falls back to inline streaming.
        - Prints per-table progress to stdout.
        - Calls sys.exit(1) if both ingestion methods fail.

    Raises:
        SystemExit: If ingestion fails for any table and the fallback
            also fails.

    Dependencies:
        TABLE_SCHEMAS, QueuedIngestClient, _streaming_ingest_fallback().

    Dependents:
        Called by main() after create_kql_tables().
    """
    # Derive the ingest endpoint from the query URI. Fabric Kusto uses a
    # separate hostname for queued ingestion (prefixed with "ingest-").
    # Query:  https://<id>.z<n>.kusto.fabric.microsoft.com
    # Ingest: https://ingest-<id>.z<n>.kusto.fabric.microsoft.com
    ingest_uri = query_uri.replace("https://", "https://ingest-")

    credential = DefaultAzureCredential()

    # Queued ingestion client handles background blob-based ingestion
    kcsb_ingest = KustoConnectionStringBuilder.with_azure_token_credential(
        ingest_uri, credential
    )

    ingest_client = QueuedIngestClient(kcsb_ingest)

    for table_name in TABLE_SCHEMAS:
        csv_path = os.path.join(DATA_DIR, f"{table_name}.csv")
        if not os.path.exists(csv_path):
            print(f"  ⚠ Skipping {table_name}.csv — file not found")
            continue

        # Reference the CSV mapping created by create_kql_tables() so the
        # ingestion engine knows column ordinals and type conversions
        mapping_name = f"{table_name}_csv_mapping"
        props = IngestionProperties(
            database=db_name,
            table=table_name,
            data_format=DataFormat.CSV,
            ingestion_mapping_reference=mapping_name,
            ignore_first_record=True,  # Skip header row in CSV
        )

        print(f"  Ingesting {table_name}.csv ...", end=" ", flush=True)
        try:
            ingest_client.ingest_from_file(csv_path, ingestion_properties=props)
            print("✓ (queued)")
        except Exception as e:
            # Queued ingestion may not be available on all Fabric SKUs
            # (e.g., F2 capacity). Fall back to inline ingestion via KQL
            # management commands, which works universally but is slower.
            print(f"⚠ queued ingestion failed: {e}")
            print(f"    Falling back to streaming ingestion...")
            if not _streaming_ingest_fallback(query_uri, db_name, table_name, csv_path):
                sys.exit(1)


def _streaming_ingest_fallback(
    query_uri: str, db_name: str, table_name: str, csv_path: str
) -> bool:
    """Ingest CSV data via .ingest inline KQL commands (fallback path).

    Purpose:
        When queued ingestion is unavailable (restricted Fabric SKU or
        networking issues), this function ingests data row-by-row via
        .ingest inline management commands. Inline ingestion is synchronous
        and limited to ~1MB per command, so data is batched.

    Parameters:
        query_uri (str): Eventhouse KQL query endpoint URI.
        db_name (str): Target KQL database name.
        table_name (str): Target table name (must already exist).
        csv_path (str): Absolute path to the CSV file to ingest.

    Returns:
        bool: True if all batches ingested successfully, False if any
        batch fails (caller should treat as fatal).

    Side effects:
        - Creates a new KustoClient connection (cannot reuse queued client).
        - Reads the entire CSV file into memory.
        - Executes one .ingest inline command per batch of 500 rows.
        - Prints per-batch progress to stdout.

    Raises:
        No exceptions propagated; errors are caught and returned as False.

    Dependencies:
        KustoClient, KustoConnectionStringBuilder.

    Dependents:
        Called by ingest_csv_files() on queued ingestion failure.
    """
    credential = DefaultAzureCredential()
    kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
        query_uri, credential
    )
    client = KustoClient(kcsb)

    # Read entire CSV into memory; inline ingestion embeds data in the command
    with open(csv_path) as f:
        lines = f.readlines()

    if len(lines) < 2:
        # Only a header row or empty file — nothing to ingest
        print(f"    ⚠ {table_name}.csv is empty")
        return True

    header = lines[0].strip()
    data_lines = [line.strip() for line in lines[1:] if line.strip()]

    # .ingest inline has a ~1MB payload limit per command. Batching at 500
    # rows keeps each command well under the limit for typical telemetry CSVs.
    batch_size = 500
    total = len(data_lines)

    for start in range(0, total, batch_size):
        batch = data_lines[start : start + batch_size]
        # The <| delimiter separates the KQL command from inline CSV data
        inline_data = "\n".join(batch)
        cmd = f".ingest inline into table {table_name} <|\n{inline_data}"

        try:
            client.execute_mgmt(db_name, cmd)
            end = min(start + batch_size, total)
            print(f"    ✓ Ingested rows {start + 1}–{end} of {total}")
        except Exception as e:
            print(f"    ✗ Inline ingest failed at row {start + 1}: {e}")
            return False

    return True


# ---------------------------------------------------------------------------
# Env file updater
# ---------------------------------------------------------------------------

def update_env_file(updates: dict[str, str]):
    """Persist key=value pairs to azure_config.env (upsert semantics).

    Purpose:
        Writes provisioning outputs (Eventhouse ID, KQL DB name, query URI)
        to the shared env file so downstream scripts and the application
        runtime can discover the Eventhouse without re-querying the API.

    Parameters:
        updates (dict[str, str]): Mapping of env var names to values.
            Existing keys are overwritten in-place; new keys are appended.

    Returns:
        None.

    Side effects:
        Reads and overwrites PROJECT_ROOT/azure_config.env. Creates the
        file if it does not exist.

    Raises:
        OSError: If the file cannot be read or written.

    Dependents:
        main() calls this to persist FABRIC_EVENTHOUSE_ID, FABRIC_KQL_DB_ID,
        FABRIC_KQL_DB_NAME, and EVENTHOUSE_QUERY_URI.
    """
    env_file = str(PROJECT_ROOT / "azure_config.env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            content = f.read()
    else:
        content = ""

    for key, value in updates.items():
        # Regex anchored to line start/end to avoid partial key matches
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            # In-place replacement preserves the key's position in the file
            content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
        else:
            # Append new keys at the end of the file
            content = content.rstrip("\n") + f"\n{key}={value}\n"

    with open(env_file, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Orchestrate end-to-end Eventhouse provisioning.

    Purpose:
        Entry point that sequences the full provisioning pipeline:
        create Eventhouse -> discover KQL DB -> create tables -> ingest data
        -> verify row counts -> persist config. Designed for idempotent
        re-runs (deletes and recreates if the Eventhouse already exists).

    Parameters:
        None. Reads module-level constants (WORKSPACE_ID, EVENTHOUSE_NAME,
        TABLE_SCHEMAS).

    Returns:
        None.

    Side effects:
        - Creates/recreates a Fabric Eventhouse via REST API.
        - Creates KQL tables and ingestion mappings via Kusto SDK.
        - Ingests CSV telemetry data into the tables.
        - Writes connection details to azure_config.env.
        - Prints detailed progress and summary to stdout.

    Raises:
        SystemExit: On missing prerequisites or any provisioning failure.
    """
    if not WORKSPACE_ID:
        print("✗ FABRIC_WORKSPACE_ID not set. Run provision_lakehouse.py first.")
        sys.exit(1)

    client = FabricClient()

    # ------------------------------------------------------------------
    # 1. Create Eventhouse
    # ------------------------------------------------------------------
    print("=" * 60)
    print(f"Provisioning Eventhouse: {EVENTHOUSE_NAME}")
    print("=" * 60)

    # Check for pre-existing Eventhouse — delete-and-recreate ensures clean
    # state (schema changes, stale data) without requiring manual cleanup
    eh = client.find_eventhouse(WORKSPACE_ID, EVENTHOUSE_NAME)
    if eh:
        print(f"  ⟳ Eventhouse already exists: {eh['id']} — deleting and recreating...")
        client.delete_eventhouse(WORKSPACE_ID, eh["id"], EVENTHOUSE_NAME)
        # Fabric needs time to release the Eventhouse name after deletion;
        # 10s is empirically sufficient for most capacities
        time.sleep(10)

    eh = client.create_eventhouse(WORKSPACE_ID, EVENTHOUSE_NAME)
    print(f"  ✓ Eventhouse created: {eh['id']}")

    eventhouse_id = eh["id"]

    # ------------------------------------------------------------------
    # 2. Discover KQL database and query URI
    #    Fabric auto-creates a default KQL DB inside each Eventhouse.
    #    We need its name (for KQL commands) and queryServiceUri (for
    #    Kusto SDK connections).
    # ------------------------------------------------------------------
    print(f"\n--- KQL Database ---")

    kql_db = client.find_kql_database(WORKSPACE_ID, eventhouse_id)
    if not kql_db:
        print("  ✗ No KQL database found — Eventhouse may still be provisioning")
        print("    Wait a minute and re-run.")
        sys.exit(1)

    db_name = kql_db["displayName"]
    db_id = kql_db["id"]
    # queryServiceUri is the Kusto-compatible endpoint for data plane operations
    query_uri = kql_db.get("properties", {}).get("queryServiceUri", "")

    print(f"  ✓ Database: {db_name} ({db_id})")
    print(f"  ✓ Query URI: {query_uri}")

    if not query_uri:
        print("  ✗ Query URI not available — Eventhouse may still be starting up")
        print("    Wait a minute and re-run.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 3. Python 3.11.7 plugin (must be enabled via Fabric portal)
    #    The .alter cluster policy sandbox command is NOT supported in
    #    Fabric Eventhouse — it only works on standalone ADX clusters.
    # ------------------------------------------------------------------
    print(f"\n--- Python Plugin (manual step) ---")
    print("  ⚠  The Python 3.11.7 language extension must be enabled on the")
    print("     Eventhouse before the anomaly detector will work.")
    print()
    print("  To enable it:")
    print("    1. Open Fabric portal → Workspace → select the Eventhouse")
    print("    2. Click 'Eventhouse' in the ribbon → 'Plugins'")
    print("    3. Toggle 'Python language extension' to ON")
    print("    4. Select Python 3.11.7 image → click 'Done'")
    print()
    print("  Note: This cannot be automated via KQL management commands in")
    print("        Fabric. Provisioning will continue — enable the plugin")
    print("        when convenient; anomaly detector queries will fail until then.")

    # ------------------------------------------------------------------
    # 4. Create KQL tables
    #    Uses the Kusto SDK (not REST API) for management commands.
    #    Requires a separate authenticated KustoClient connection.
    # ------------------------------------------------------------------
    credential = DefaultAzureCredential()
    kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
        query_uri, credential
    )
    kusto_client = KustoClient(kcsb)

    print(f"\n--- Creating KQL tables ---")

    create_kql_tables(kusto_client, db_name)

    # ------------------------------------------------------------------
    # 5. Ingest CSV data
    # ------------------------------------------------------------------
    print(f"\n--- Ingesting CSV data ---")

    ingest_csv_files(query_uri, db_name)

    # ------------------------------------------------------------------
    # 6. Verify row counts
    #    Queued ingestion is asynchronous — rows may not appear immediately.
    #    A short delay improves the chance of seeing accurate counts.
    # ------------------------------------------------------------------
    print(f"\n--- Verifying ingestion ---")

    # Queued ingestion processes asynchronously; 15s covers typical latency
    print("  Waiting 15s for queued ingestion to process...")
    time.sleep(15)

    for table_name in TABLE_SCHEMAS:
        try:
            result = kusto_client.execute_query(db_name, f"{table_name} | count")
            for row in result.primary_results[0]:
                count = row[0]
                print(f"  ✓ {table_name}: {count} rows")
        except Exception as e:
            # Non-fatal: count may fail if queued ingestion is still processing
            print(f"  ⚠ {table_name}: could not verify — {e}")

    # ------------------------------------------------------------------
    # 7. Update azure_config.env
    #    Persist Eventhouse connection details so downstream consumers
    #    (deploy scripts, app runtime) can discover the Eventhouse.
    # ------------------------------------------------------------------
    env_updates = {
        "FABRIC_EVENTHOUSE_ID": eventhouse_id,
        "FABRIC_KQL_DB_ID": db_id,
        "FABRIC_KQL_DB_NAME": db_name,
        "EVENTHOUSE_QUERY_URI": query_uri,
    }
    update_env_file(env_updates)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("✅ Eventhouse provisioning complete!")
    print(f"   Eventhouse : {EVENTHOUSE_NAME} ({eventhouse_id})")
    print(f"   KQL DB     : {db_name} ({db_id})")
    print(f"   Query URI  : {query_uri}")
    print(f"   Tables     : {', '.join(TABLE_SCHEMAS.keys())}")
    print("=" * 60)

    print("\n  ✓ Updated azure_config.env")
    for key, value in env_updates.items():
        print(f"    {key}={value}")
    print()


if __name__ == "__main__":
    main()
