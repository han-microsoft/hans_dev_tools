"""
Provision a Microsoft Fabric Lakehouse, upload entity CSVs, and load them as delta tables.

Module role:
    Automates Step 1.4 of the infrastructure provisioning plan. Handles
    the full lifecycle of a Fabric Lakehouse within an already-provisioned
    workspace: idempotent create-or-replace, file upload via OneLake
    ADLS Gen2 endpoint, and table ingestion via the Fabric REST API's
    Lakehouse Tables load endpoint.

    Orchestration sequence:
      1. Validate that the Fabric workspace exists (created by provision_workspace.py).
      2. Create (or delete-and-recreate) the Lakehouse item.
      3. Upload entity CSVs to the Lakehouse's OneLake Files/ folder.
      4. Invoke the Tables load API to materialise each CSV as a managed
         delta table inside the Lakehouse.
      5. Persist the resulting FABRIC_LAKEHOUSE_ID back to azure_config.env.

Key collaborators:
    - _config.py            — centralised env-loading, Fabric API base URL,
                              workspace/capacity/lakehouse names, PROJECT_ROOT.
    - graph_schema.yaml     — per-scenario schema that declares vertices and
                              edges; each entry's ``csv_file`` field drives
                              the LAKEHOUSE_TABLES list so no table names are
                              hardcoded in this module.
    - provision_workspace.py — must run first to create the workspace and
                               populate FABRIC_WORKSPACE_ID.
    - provision_eventhouse.py — runs *after* this script to create the KQL
                                Eventhouse and shortcut to the same Lakehouse.

Dependents:
    Called by: manual invocation (``uv run provision_lakehouse.py``),
    deploy.sh orchestration script, CI pipelines.

Prerequisites:
    - Fabric capacity deployed (Step 1.3 via ``azd up``).
    - Tenant settings enabled (Step 1.1).
    - Entity CSV data generated (Step 1.2).
    - azure_config.env populated with FABRIC_WORKSPACE_ID, WORKSPACE_NAME,
      CAPACITY_ID, LAKEHOUSE_NAME, and DEFAULT_SCENARIO.

Usage:
    uv run provision_lakehouse.py
"""

import os
import re
import sys
import time

import requests
import yaml
from azure.identity import DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient

from _config import (
    FABRIC_API, PROJECT_ROOT, DATA_DIR,
    WORKSPACE_ID, WORKSPACE_NAME, CAPACITY_ID, LAKEHOUSE_NAME,
)

# ---------------------------------------------------------------------------
# Module-scope Configuration
# ---------------------------------------------------------------------------

# Fixed OneLake account name — Microsoft Fabric exposes every tenant's
# lakehouse storage under the same ``onelake.dfs.fabric.microsoft.com``
# hostname.  Changing this value would break the ADLS Gen2 upload path.
ONELAKE_ACCOUNT = "onelake"
ONELAKE_URL = f"https://{ONELAKE_ACCOUNT}.dfs.fabric.microsoft.com"

# DEFAULT_SCENARIO selects which graph_schema.yaml and entity CSVs to use.
# Without it the script cannot locate data files, so we fail immediately.
SCENARIO = os.environ.get("DEFAULT_SCENARIO", "")
if not SCENARIO:
    print("ERROR: DEFAULT_SCENARIO not set"); sys.exit(1)

# Absolute path to the directory containing entity CSV files for the
# active scenario.  Passed to upload_csvs_to_onelake() at runtime.
LAKEHOUSE_CSV_DIR = str(DATA_DIR / "scenarios" / SCENARIO / "data" / "entities")

# ---------------------------------------------------------------------------
# Derive table list from graph_schema.yaml (no hardcoded table names)
# ---------------------------------------------------------------------------
# The schema file declares every vertex and edge type together with the
# CSV file that holds its data (e.g. ``csv_file: DimRouter.csv``).
# Stripping the ``.csv`` suffix yields the managed delta table name.
# Using a set for deduplication preserves insertion order via the list.
_SCHEMA_PATH = DATA_DIR / "scenarios" / SCENARIO / "graph_schema.yaml"
if not _SCHEMA_PATH.exists():
    print(f"ERROR: graph_schema.yaml not found: {_SCHEMA_PATH}"); sys.exit(1)

with open(_SCHEMA_PATH) as _f:
    _GRAPH_SCHEMA = yaml.safe_load(_f)

# Collect unique table names — vertex CSV files (Dim*) first, then edge
# CSV files (Fact*).  Order matters only cosmetically for console output.
_seen: set[str] = set()
LAKEHOUSE_TABLES: list[str] = []
for vertex in _GRAPH_SCHEMA.get("vertices", []):
    table = vertex["csv_file"].removesuffix(".csv")
    if table not in _seen:
        _seen.add(table)
        LAKEHOUSE_TABLES.append(table)
for edge in _GRAPH_SCHEMA.get("edges", []):
    table = edge["csv_file"].removesuffix(".csv")
    if table not in _seen:
        _seen.add(table)
        LAKEHOUSE_TABLES.append(table)


class FabricClient:
    """Authenticated client for the Microsoft Fabric REST API.

    Purpose:
        Encapsulates OAuth token acquisition and provides typed methods for
        Lakehouse CRUD operations and table loading.  Handles Fabric's
        asynchronous (202 / long-running operation) response pattern
        transparently so callers receive the final result dict.

    Role in system:
        Data-access / infrastructure layer.  Used exclusively by main() in
        this module and could be imported by other provisioning scripts that
        need Lakehouse operations.

    Lifecycle:
        Instantiated once per script run.  Acquires a fresh Azure AD token
        on every request via DefaultAzureCredential (no caching), so the
        instance may be long-lived without token-expiry concerns.

    Key collaborators:
        - ``_config.FABRIC_API`` — base URL for all REST calls.
        - ``azure.identity.DefaultAzureCredential`` — token provider.
        - Fabric REST API v1 — lakehouses, operations, and table-load
          endpoints.
    """

    def __init__(self):
        """Initialise credential provider.  No network call is made until
        the first API request."""
        # DefaultAzureCredential tries managed identity, CLI, env vars, etc.
        self.credential = DefaultAzureCredential()
        self._token = None  # Unused cache slot; tokens are fetched per-call

    def _get_token(self) -> str:
        """Acquire a fresh OAuth2 bearer token for the Fabric API scope.

        Returns:
            str: Raw JWT access token string.

        Side effects:
            May trigger interactive browser login on first call if no
            cached credential is available.
        """
        token = self.credential.get_token("https://api.fabric.microsoft.com/.default")
        return token.token

    @property
    def headers(self) -> dict:
        """Build HTTP headers with a fresh bearer token for Fabric API calls.

        Returns:
            dict: ``Authorization`` and ``Content-Type`` headers suitable for
            ``requests.post/get/delete`` calls.
        """
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def _wait_for_lro(self, response: requests.Response, label: str, timeout: int = 300):
        """Poll a Fabric long-running operation (LRO) until completion.

        Fabric returns 202 Accepted for asynchronous operations and provides
        an ``x-ms-operation-id`` header to poll for status.  This method
        abstracts that pattern so callers can treat operations as synchronous.

        Parameters:
            response (requests.Response): Initial HTTP response from the
                create/load call.  Must be 201 (immediate success) or 202
                (accepted, poll required).
            label (str): Human-readable description for log/error messages
                (e.g. ``"Create Lakehouse 'NetworkTopologyLH'"``).
            timeout (int): Maximum seconds to poll before aborting. Defaults
                to 300 (5 minutes).

        Returns:
            dict: Parsed JSON body of the completed operation result.

        Side effects:
            Calls ``sys.exit(1)`` on non-202/201 status, missing operation
            ID, operation failure/cancellation, or timeout.  This is
            intentional — provisioning scripts should halt on any error.

        Raises:
            SystemExit: On any unrecoverable failure condition.
        """
        # 201 means the resource was created synchronously — return immediately
        if response.status_code == 201:
            return response.json()

        # Any status other than 202 is an unexpected failure
        if response.status_code != 202:
            print(f"  ✗ {label} failed: {response.status_code} — {response.text}")
            sys.exit(1)

        # Extract the operation ID from response headers for polling
        operation_id = response.headers.get("x-ms-operation-id")
        if not operation_id:
            print(f"  ✗ {label}: no operation ID in 202 response")
            sys.exit(1)

        url = f"{FABRIC_API}/operations/{operation_id}"
        # Honour the server's suggested polling interval; default 5s if absent
        retry_after = int(response.headers.get("Retry-After", "5"))

        elapsed = 0
        while elapsed < timeout:
            time.sleep(retry_after)
            elapsed += retry_after
            r = requests.get(url, headers=self.headers)
            # Transient poll failures (e.g. 429, 500) are silently retried
            if r.status_code != 200:
                continue
            status = r.json().get("status", "")
            if status == "Succeeded":
                # Fabric exposes the created resource at /operations/{id}/result
                result_url = f"{url}/result"
                rr = requests.get(result_url, headers=self.headers)
                if rr.status_code == 200:
                    return rr.json()
                # Fall back to the operation payload if /result is unavailable
                return r.json()
            elif status in ("Failed", "Cancelled"):
                print(f"  ✗ {label} {status}: {r.json()}")
                sys.exit(1)

        print(f"  ✗ {label} timed out after {timeout}s")
        sys.exit(1)

    # --- Lakehouse CRUD ---

    def find_lakehouse(self, workspace_id: str, name: str) -> dict | None:
        """Search for an existing Lakehouse by display name within a workspace.

        Parameters:
            workspace_id (str): GUID of the Fabric workspace.
            name (str): Display name to match (case-sensitive).

        Returns:
            dict | None: Lakehouse item dict (contains ``id``,
            ``displayName``, etc.) if found, otherwise ``None``.

        Raises:
            requests.HTTPError: If the list-lakehouses call fails.
        """
        r = requests.get(f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses", headers=self.headers)
        r.raise_for_status()
        for item in r.json().get("value", []):
            if item["displayName"] == name:
                return item
        return None

    def delete_lakehouse(self, workspace_id: str, lakehouse_id: str, name: str):
        """Delete a Lakehouse by its item ID.

        Parameters:
            workspace_id (str): GUID of the owning workspace.
            lakehouse_id (str): GUID of the Lakehouse to delete.
            name (str): Display name, used only for log messages.

        Side effects:
            Prints status to stdout.  Does NOT call ``sys.exit`` on failure
            — the caller decides whether to abort.  Fabric needs ~15 s
            after deletion before the display name becomes available for
            reuse; the caller is responsible for that delay.
        """
        r = requests.delete(
            f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}",
            headers=self.headers,
        )
        if r.status_code in (200, 204):
            print(f"  ✓ Deleted existing Lakehouse: {name} ({lakehouse_id})")
        else:
            print(f"  ⚠ Delete Lakehouse failed: {r.status_code} — {r.text}")
            print(f"    Continuing anyway...")

    # Fabric error codes returned when a recently-deleted item's display name
    # has not yet been released from the namespace.  The set covers observed
    # variations across Fabric API versions.
    _NAME_CONFLICT_CODES = {
        "ItemDisplayNameNotAvailableYet",
        "DatamartCreationFailedDueToBadRequest",  # Fabric sometimes uses this misleading code
        "ItemDisplayNameAlreadyInUse",
    }

    def create_lakehouse(self, workspace_id: str, name: str, max_retries: int = 12, retry_delay: int = 30) -> dict:
        """Create a new Lakehouse, retrying if the name is temporarily held.

        After a Lakehouse is deleted, Fabric may take several minutes to
        release its display name.  This method retries on known name-conflict
        error codes to make delete-then-recreate idempotent.

        Parameters:
            workspace_id (str): GUID of the workspace.
            name (str): Desired display name for the Lakehouse.
            max_retries (int): Maximum number of retry attempts on
                name-conflict errors.  Defaults to 12.
            retry_delay (int): Seconds to wait between retries.
                Defaults to 30.  Total wait budget = max_retries × retry_delay.

        Returns:
            dict: Fabric Lakehouse item dict with at least ``id`` and
            ``displayName`` keys.

        Side effects:
            Calls ``sys.exit(1)`` if the name is never released within the
            retry budget or if the underlying LRO fails.

        Raises:
            SystemExit: On exhausted retries or LRO failure.
        """
        body = {"displayName": name, "description": f"Lakehouse for {WORKSPACE_NAME}"}
        url = f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses"

        for attempt in range(1, max_retries + 1):
            r = requests.post(url, headers=self.headers, json=body)

            # Check for name-conflict errors that are retryable
            if r.status_code == 400:
                try:
                    err = r.json()
                    error_code = err.get("errorCode", "")
                    error_msg = err.get("message", "").lower()
                except Exception:
                    error_code = ""
                    error_msg = ""

                # Match against known error codes or message substrings
                name_held = (
                    error_code in self._NAME_CONFLICT_CODES
                    or "name is already in use" in error_msg
                    or "name not available" in error_msg
                )
                if name_held:
                    print(f"  ⏳ Name not released yet (attempt {attempt}/{max_retries}), retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    continue

            # For any non-retryable response, delegate to LRO handler
            return self._wait_for_lro(r, f"Create Lakehouse '{name}'")

        print(f"  ✗ Lakehouse name '{name}' still not available after {max_retries} attempts ({max_retries * retry_delay}s)")
        sys.exit(1)

    # --- Lakehouse Table Loading ---

    def load_table(
        self, workspace_id: str, lakehouse_id: str, table_name: str, relative_path: str
    ):
        """Load a CSV from the Lakehouse Files/ folder into a managed delta table.

        Uses the Fabric ``/tables/{table_name}/load`` endpoint which
        reads a file already present in OneLake and materialises it as a
        Delta-format table managed by the Lakehouse engine.

        Parameters:
            workspace_id (str): GUID of the workspace.
            lakehouse_id (str): GUID of the Lakehouse item.
            table_name (str): Target delta table name (e.g. ``DimRouter``).
            relative_path (str): Path relative to the Lakehouse root
                (e.g. ``Files/DimRouter.csv``).

        Side effects:
            Creates or overwrites the named delta table.  Blocks until
            the long-running operation completes (via ``_wait_for_lro``).
            Calls ``sys.exit(1)`` on failure.
        """
        # Table load body — Overwrite mode ensures idempotency on re-runs
        body = {
            "relativePath": relative_path,
            "pathType": "File",
            "mode": "Overwrite",
            "formatOptions": {"format": "Csv", "header": True, "delimiter": ","},
        }
        r = requests.post(
            f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables/{table_name}/load",
            headers=self.headers,
            json=body,
        )
        self._wait_for_lro(r, f"Load table '{table_name}'")


def upload_csvs_to_onelake(
    workspace_name: str, lakehouse_name: str, csv_dir: str, file_names: list[str]
):
    """Upload entity CSV files to the Lakehouse Files/ folder via OneLake ADLS Gen2 API.

    OneLake exposes every Fabric Lakehouse as an Azure Data Lake Storage
    Gen2 filesystem.  This function authenticates via DefaultAzureCredential
    and uploads each named CSV into ``<lakehouse>.Lakehouse/Files/``.

    Parameters:
        workspace_name (str): Fabric workspace display name — used as the
            ADLS Gen2 filesystem (container) name.
        lakehouse_name (str): Lakehouse display name — used to construct the
            remote directory path ``<name>.Lakehouse/Files``.
        csv_dir (str): Local directory containing the CSV files to upload.
        file_names (list[str]): Base names (without ``.csv`` extension) of
            the files to upload.  Derived from LAKEHOUSE_TABLES.

    Side effects:
        Creates or overwrites files in OneLake.  Prints per-file status to
        stdout.  Missing local files are logged and skipped (not fatal).

    Dependencies:
        - ``azure.storage.filedatalake.DataLakeServiceClient`` — ADLS Gen2
          SDK for file upload.
        - ``ONELAKE_URL`` — module-level constant for the OneLake endpoint.
    """
    credential = DefaultAzureCredential()
    # DataLakeServiceClient connects to the OneLake DFS endpoint
    service_client = DataLakeServiceClient(ONELAKE_URL, credential=credential)
    # The filesystem name in OneLake corresponds to the workspace display name
    fs_client = service_client.get_file_system_client(workspace_name)
    # Remote directory path follows Fabric's naming convention
    data_path = f"{lakehouse_name}.Lakehouse/Files"

    for name in file_names:
        file_path = os.path.join(csv_dir, f"{name}.csv")
        if not os.path.exists(file_path):
            print(f"  ⚠ Skipping {name}.csv — file not found")
            continue

        remote_path = f"{data_path}/{name}.csv"
        dir_client = fs_client.get_directory_client(data_path)
        file_client = dir_client.get_file_client(f"{name}.csv")

        # Upload with overwrite=True for idempotent re-runs
        with open(file_path, "rb") as f:
            file_client.upload_data(f, overwrite=True)
        print(f"  ✓ Uploaded {name}.csv → OneLake Files/")


def main():
    """Orchestrate end-to-end Lakehouse provisioning, upload, and table loading.

    Execution sequence:
      1. Validate that FABRIC_WORKSPACE_ID is set (proof that provision_workspace.py ran).
      2. Find or delete-and-recreate the Lakehouse to ensure a clean state.
      3. Upload all entity CSVs to OneLake Files/ via ADLS Gen2.
      4. Invoke the Fabric table-load API for each CSV → delta table.
      5. Persist FABRIC_WORKSPACE_ID and FABRIC_LAKEHOUSE_ID to azure_config.env
         so downstream scripts (provision_eventhouse.py, app runtime) can discover them.

    Side effects:
        Mutates Fabric resources (Lakehouse create/delete, file uploads, table loads).
        Writes to azure_config.env on disk.  Calls ``sys.exit(1)`` on any failure.

    Raises:
        SystemExit: If workspace ID is missing or any provisioning step fails.
    """
    client = FabricClient()

    # ------------------------------------------------------------------
    # 1. Validate workspace exists (created by provision_workspace.py)
    # ------------------------------------------------------------------
    if not WORKSPACE_ID:
        print("✗ FABRIC_WORKSPACE_ID not set. Run provision_workspace.py first.")
        sys.exit(1)

    workspace_id = WORKSPACE_ID
    print("=" * 60)
    print(f"Provisioning Lakehouse in workspace: {WORKSPACE_NAME} ({workspace_id})")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 2. Lakehouse — idempotent create-or-replace
    # ------------------------------------------------------------------
    print(f"\n--- Lakehouse: {LAKEHOUSE_NAME} ---")

    lh = client.find_lakehouse(workspace_id, LAKEHOUSE_NAME)
    if lh:
        print(f"  ⟳ Lakehouse already exists: {lh['id']} — deleting and recreating...")
        client.delete_lakehouse(workspace_id, lh["id"], LAKEHOUSE_NAME)
        # Fabric needs ~15 s to release the display name after deletion
        time.sleep(15)

    lh = client.create_lakehouse(workspace_id, LAKEHOUSE_NAME)
    print(f"  ✓ Lakehouse created: {lh['id']}")

    lakehouse_id = lh["id"]

    # ------------------------------------------------------------------
    # 3. Upload CSVs to Lakehouse Files/ via OneLake ADLS Gen2 endpoint
    # ------------------------------------------------------------------
    print(f"\n--- Uploading CSVs to Lakehouse OneLake ---")
    upload_csvs_to_onelake(WORKSPACE_NAME, LAKEHOUSE_NAME, LAKEHOUSE_CSV_DIR, LAKEHOUSE_TABLES)

    # ------------------------------------------------------------------
    # 4. Load each CSV into a managed delta table via the Tables API
    # ------------------------------------------------------------------
    print(f"\n--- Loading CSVs into managed delta tables ---")
    for table_name in LAKEHOUSE_TABLES:
        # ``Files/`` prefix is relative to the Lakehouse root
        relative_path = f"Files/{table_name}.csv"
        client.load_table(workspace_id, lakehouse_id, table_name, relative_path)
        print(f"  ✓ Loaded table: {table_name}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("=" * 60)
    print("✅ Fabric provisioning complete!")
    print(f"   Workspace : {WORKSPACE_NAME} ({workspace_id})")
    print(f"   Lakehouse : {LAKEHOUSE_NAME} ({lakehouse_id})")
    print("=" * 60)
    print("\n  Next: run 'uv run provision_eventhouse.py' for Eventhouse setup")

    # ------------------------------------------------------------------
    # 5. Persist IDs to azure_config.env for downstream consumers
    # ------------------------------------------------------------------
    env_file = str(PROJECT_ROOT / "azure_config.env")
    env_additions = {
        "FABRIC_WORKSPACE_ID": workspace_id,
        "FABRIC_LAKEHOUSE_ID": lakehouse_id,
    }

    if os.path.exists(env_file):
        with open(env_file) as f:
            content = f.read()
    else:
        content = ""

    # Upsert each key: replace if present, append if absent
    for key, value in env_additions.items():
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
        else:
            content = content.rstrip("\n") + f"\n{key}={value}\n"

    with open(env_file, "w") as f:
        f.write(content)

    print("\n  ✓ Updated azure_config.env:")
    for key, value in env_additions.items():
        print(f"    {key}={value}")


if __name__ == "__main__":
    main()
