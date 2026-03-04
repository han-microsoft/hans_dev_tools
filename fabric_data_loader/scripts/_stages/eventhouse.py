"""Stage 4: Eventhouse — create, build KQL tables, ingest telemetry.

Module role:
    Provisions a Fabric Eventhouse inside the target folder, discovers its
    auto-created KQL database, creates tables from scenario.yaml definitions,
    and ingests CSV telemetry data via queued or inline ingestion.

Key collaborators:
    - ``_deploy_client.FabricDeployClient`` — item CRUD.
    - ``_deploy_manifest.DeployManifest``   — paths, names, IDs.
    - ``scenario.yaml``                     — telemetry container definitions.

Dependents:
    Ontology stage requires ``manifest.eventhouse_id``, ``manifest.kql_db_name``,
    and ``manifest.kql_query_uri``.
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path

import yaml
from azure.identity import AzureCliCredential, ClientSecretCredential, DefaultAzureCredential
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.data.data_format import DataFormat
from azure.kusto.ingest import QueuedIngestClient, IngestionProperties

from _deploy_client import FabricDeployClient
from _deploy_manifest import DeployManifest


# ---------------------------------------------------------------------------
# Schema derivation from scenario.yaml + CSV headers
# ---------------------------------------------------------------------------

def _build_table_schemas(
    scenario_yaml_path: str, telemetry_dir: str
) -> dict[str, dict[str, str]]:
    """Derive KQL table schemas from scenario.yaml and CSV header rows.

    Parameters:
        scenario_yaml_path: Path to scenario.yaml.
        telemetry_dir: Path to the telemetry CSV directory.

    Returns:
        Mapping of table_name → {column_name: kql_type}.
        Types: ``datetime`` (Timestamp column), ``real`` (numeric_fields), ``string`` (default).
    """
    with open(scenario_yaml_path) as f:
        cfg = yaml.safe_load(f)

    containers = (
        cfg.get("data_sources", {})
        .get("telemetry", {})
        .get("config", {})
        .get("containers", [])
    )

    schemas: dict[str, dict[str, str]] = {}
    for container in containers:
        table_name = container["name"]
        csv_file = container.get("csv_file", f"{table_name}.csv")
        csv_path = os.path.join(telemetry_dir, csv_file)
        numeric = set(container.get("numeric_fields", []))

        if not os.path.exists(csv_path):
            print(f"  ⚠ CSV not found for table {table_name}: {csv_path}")
            continue

        with open(csv_path) as f:
            reader = csv.reader(f)
            header = next(reader, None)

        if not header:
            print(f"  ⚠ Empty CSV for table {table_name}")
            continue

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


# ---------------------------------------------------------------------------
# KQL table creation
# ---------------------------------------------------------------------------

def _create_kql_tables(
    kusto_client: KustoClient, db_name: str, schemas: dict[str, dict[str, str]]
) -> None:
    """Create KQL tables and CSV ingestion mappings.

    Parameters:
        kusto_client: Authenticated Kusto client.
        db_name: Target KQL database name.
        schemas: Table schemas from ``_build_table_schemas()``.
    """
    # Create tables
    for table_name, schema in schemas.items():
        columns = ", ".join(f"['{col}']: {dtype}" for col, dtype in schema.items())
        cmd = f".create-merge table {table_name} ({columns})"
        print(f"  Creating table: {table_name} ...", end=" ")
        try:
            kusto_client.execute_mgmt(db_name, cmd)
            print("✓")
        except Exception as e:
            print(f"✗ {e}")
            raise

    # Create CSV ingestion mappings
    for table_name, schema in schemas.items():
        mapping_name = f"{table_name}_csv_mapping"
        mapping_json = ", ".join(
            f'{{"Name": "{col}", "DataType": "{dtype}", "Ordinal": {i}}}'
            for i, (col, dtype) in enumerate(schema.items())
        )
        cmd = (
            f".create-or-alter table {table_name} ingestion csv mapping "
            f"'{mapping_name}' '[{mapping_json}]'"
        )
        print(f"  CSV mapping: {mapping_name} ...", end=" ")
        try:
            kusto_client.execute_mgmt(db_name, cmd)
            print("✓")
        except Exception as e:
            print(f"✗ {e}")
            raise


# ---------------------------------------------------------------------------
# Data ingestion
# ---------------------------------------------------------------------------

def _build_kusto_credential(manifest: DeployManifest):
    """Build credential for Kusto SDK connections."""
    if manifest.tenant_id and manifest.client_id and manifest.client_secret:
        return ClientSecretCredential(
            manifest.tenant_id, manifest.client_id, manifest.client_secret
        )
    if manifest.tenant_id:
        return AzureCliCredential(tenant_id=manifest.tenant_id)
    if os.environ.get("WEBSITE_INSTANCE_ID") or os.environ.get("KUBERNETES_SERVICE_HOST"):
        return DefaultAzureCredential()
    return AzureCliCredential()


def _ingest_csv_files(
    manifest: DeployManifest,
    query_uri: str,
    db_name: str,
    schemas: dict[str, dict[str, str]],
) -> None:
    """Ingest telemetry CSVs into KQL tables via queued ingestion with fallback.

    Parameters:
        manifest: Deploy manifest with telemetry CSV path.
        query_uri: Eventhouse KQL query endpoint URI.
        db_name: KQL database name.
        schemas: Table schemas (keys = table names).
    """
    ingest_uri = query_uri.replace("https://", "https://ingest-")
    credential = _build_kusto_credential(manifest)
    telemetry_dir = manifest.telemetry_csv_dir

    kcsb_ingest = KustoConnectionStringBuilder.with_azure_token_credential(
        ingest_uri, credential
    )
    ingest_client = QueuedIngestClient(kcsb_ingest)

    for table_name in schemas:
        csv_path = os.path.join(telemetry_dir, f"{table_name}.csv")
        if not os.path.exists(csv_path):
            print(f"  ⚠ Skipping {table_name}.csv — not found")
            continue

        mapping_name = f"{table_name}_csv_mapping"
        props = IngestionProperties(
            database=db_name,
            table=table_name,
            data_format=DataFormat.CSV,
            ingestion_mapping_reference=mapping_name,
            ignore_first_record=True,
        )

        print(f"  Ingesting {table_name}.csv ...", end=" ", flush=True)
        try:
            ingest_client.ingest_from_file(csv_path, ingestion_properties=props)
            print("✓ (queued)")
        except Exception as e:
            print(f"⚠ queued ingestion failed: {e}")
            print(f"    Falling back to inline ingestion...")
            _inline_ingest(manifest, query_uri, db_name, table_name, csv_path)


def _inline_ingest(
    manifest: DeployManifest,
    query_uri: str,
    db_name: str,
    table_name: str,
    csv_path: str,
) -> None:
    """Fallback: ingest data via .ingest inline KQL commands.

    Parameters:
        manifest: Deploy manifest with auth config.
        query_uri: KQL query endpoint URI.
        db_name: KQL database name.
        table_name: Target table.
        csv_path: Path to the CSV file.
    """
    credential = _build_kusto_credential(manifest)
    kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
        query_uri, credential
    )
    client = KustoClient(kcsb)

    with open(csv_path) as f:
        lines = f.readlines()

    if len(lines) < 2:
        print(f"    ⚠ {table_name}.csv is empty")
        return

    data_lines = [line.strip() for line in lines[1:] if line.strip()]
    batch_size = 500
    total = len(data_lines)

    for start in range(0, total, batch_size):
        batch = data_lines[start : start + batch_size]
        inline_data = "\n".join(batch)
        cmd = f".ingest inline into table {table_name} <|\n{inline_data}"
        try:
            client.execute_mgmt(db_name, cmd)
            end = min(start + batch_size, total)
            print(f"    ✓ Ingested rows {start + 1}–{end} of {total}")
        except Exception as e:
            print(f"    ✗ Inline ingest failed at row {start + 1}: {e}")
            raise


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def run(client: FabricDeployClient, manifest: DeployManifest) -> None:
    """Execute the eventhouse stage: create, tables, ingest.

    Parameters:
        client: Authenticated Fabric REST client.
        manifest: Deploy manifest — ``eventhouse_id``, ``kql_db_name``,
            ``kql_query_uri`` set on completion.
    """
    print("\n--- Stage 4: Eventhouse ---")

    # Check for scenario.yaml (needed for telemetry container definitions)
    if not manifest.scenario_yaml_path or not Path(manifest.scenario_yaml_path).exists():
        print(f"  ⚠ scenario.yaml not found: {manifest.scenario_yaml_path}")
        print("    Skipping eventhouse stage")
        return

    schemas = _build_table_schemas(
        manifest.scenario_yaml_path, manifest.telemetry_csv_dir
    )
    if not schemas:
        print("  ⚠ No telemetry tables found in scenario.yaml — skipping")
        return

    print(f"  Tables to provision: {', '.join(schemas.keys())}")

    # Find or create Eventhouse
    existing = client.find_item(
        manifest.workspace_id, "Eventhouse", manifest.eventhouse_name
    )

    if existing and manifest.force:
        print(f"  ⟳ Deleting existing Eventhouse: {existing['id']}...")
        client.delete_item(
            manifest.workspace_id, existing["id"], manifest.eventhouse_name
        )
        time.sleep(10)
        existing = None

    if existing:
        print(f"  ✓ Eventhouse already exists: {existing['id']}")
        manifest.eventhouse_id = existing["id"]
    else:
        print(f"  Creating Eventhouse: {manifest.eventhouse_name}...")
        result = client.create_item(
            workspace_id=manifest.workspace_id,
            item_type="Eventhouse",
            name=manifest.eventhouse_name,
            folder_id=manifest.folder_id or None,
            description=f"Telemetry data for {manifest.scenario}",
        )
        manifest.eventhouse_id = result["id"]
        print(f"  ✓ Eventhouse created: {manifest.eventhouse_id}")

    # Discover KQL database
    print(f"\n  Discovering KQL database...")
    kql_db = client.find_kql_database_for_eventhouse(
        manifest.workspace_id, manifest.eventhouse_id
    )
    if not kql_db:
        print("  ✗ No KQL database found — Eventhouse may still be provisioning")
        print("    Wait a minute and retry")
        return

    manifest.kql_db_name = kql_db["displayName"]
    manifest.kql_query_uri = kql_db.get("properties", {}).get("queryServiceUri", "")
    print(f"  ✓ KQL DB: {manifest.kql_db_name}")
    print(f"  ✓ Query URI: {manifest.kql_query_uri}")

    if not manifest.kql_query_uri:
        print("  ✗ Query URI not available yet — retry shortly")
        return

    if client.dry_run:
        for t in schemas:
            print(f"  [DRY RUN] Would create table + ingest: {t}")
        return

    # Create KQL tables and mappings
    print(f"\n  Creating KQL tables...")
    credential = _build_kusto_credential(manifest)
    kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
        manifest.kql_query_uri, credential
    )
    kusto_client = KustoClient(kcsb)
    _create_kql_tables(kusto_client, manifest.kql_db_name, schemas)

    # Ingest CSV data
    print(f"\n  Ingesting telemetry data...")
    _ingest_csv_files(manifest, manifest.kql_query_uri, manifest.kql_db_name, schemas)
