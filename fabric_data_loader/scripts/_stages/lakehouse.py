"""Stage 3: Lakehouse — create, upload CSVs, load delta tables.

Module role:
    Provisions a Fabric Lakehouse inside the target folder, uploads entity
    CSVs to OneLake via the ADLS Gen2 endpoint, and materialises each CSV
    as a managed delta table via the Tables load API.

Key collaborators:
    - ``_deploy_client.FabricDeployClient`` — item CRUD, table load.
    - ``_deploy_manifest.DeployManifest``   — paths, names, IDs.
    - ``graph_schema.yaml``                 — vertex/edge CSV file names.

Dependents:
    Ontology stage requires ``manifest.lakehouse_id``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import yaml
from azure.identity import AzureCliCredential, ClientSecretCredential, DefaultAzureCredential
from azure.storage.filedatalake import DataLakeServiceClient

from _deploy_client import FabricDeployClient
from _deploy_manifest import DeployManifest


def _load_table_list(schema_path: str) -> list[str]:
    """Extract unique table names from graph_schema.yaml.

    Reads vertex and edge definitions, strips ``.csv`` suffix from
    ``csv_file`` fields, and returns a deduplicated list preserving
    declaration order.

    Parameters:
        schema_path: Absolute path to ``graph_schema.yaml``.

    Returns:
        List of table name strings (e.g. ``["DimCoreRouter", "FactConnects"]``).
    """
    with open(schema_path) as f:
        schema = yaml.safe_load(f)

    seen: set[str] = set()
    tables: list[str] = []
    for vertex in schema.get("vertices", []):
        table = vertex["csv_file"].removesuffix(".csv")
        if table not in seen:
            seen.add(table)
            tables.append(table)
    for edge in schema.get("edges", []):
        table = edge["csv_file"].removesuffix(".csv")
        if table not in seen:
            seen.add(table)
            tables.append(table)
    return tables


def _build_onelake_credential(manifest: DeployManifest):
    """Build a credential suitable for OneLake ADLS Gen2 uploads.

    Uses the same auth strategy as the Fabric REST client, but the
    credential may differ (e.g. OneLake uses storage scope internally).

    Parameters:
        manifest: Deploy manifest with auth config.

    Returns:
        A ``TokenCredential`` instance for OneLake access.
    """
    if manifest.tenant_id and manifest.client_id and manifest.client_secret:
        return ClientSecretCredential(
            manifest.tenant_id, manifest.client_id, manifest.client_secret
        )
    if manifest.tenant_id:
        return AzureCliCredential(tenant_id=manifest.tenant_id)
    if os.environ.get("WEBSITE_INSTANCE_ID") or os.environ.get("KUBERNETES_SERVICE_HOST"):
        return DefaultAzureCredential()
    return AzureCliCredential()


def _upload_csvs(
    manifest: DeployManifest,
    lakehouse_name: str,
    tables: list[str],
) -> None:
    """Upload entity CSV files to the Lakehouse Files/ folder via OneLake.

    Parameters:
        manifest: Deploy manifest with OneLake endpoint and auth config.
        lakehouse_name: Lakehouse display name (used in the OneLake path).
        tables: List of table names (CSV filenames without extension).

    Side effects:
        Uploads files to OneLake. Prints per-file progress.
    """
    credential = _build_onelake_credential(manifest)

    # Use the workspace-specific OneLake DFS endpoint if available (cross-tenant)
    onelake_url = manifest.onelake_dfs_endpoint
    if not onelake_url:
        onelake_url = "https://onelake.dfs.fabric.microsoft.com"

    service_client = DataLakeServiceClient(onelake_url, credential=credential)
    # The filesystem name corresponds to the workspace display name
    fs_client = service_client.get_file_system_client(manifest.workspace_name)
    # Fabric convention: <LakehouseName>.Lakehouse/Files/
    data_path = f"{lakehouse_name}.Lakehouse/Files"

    csv_dir = manifest.entity_csv_dir

    for name in tables:
        file_path = os.path.join(csv_dir, f"{name}.csv")
        if not os.path.exists(file_path):
            print(f"  ⚠ Skipping {name}.csv — file not found at {file_path}")
            continue

        dir_client = fs_client.get_directory_client(data_path)
        file_client = dir_client.get_file_client(f"{name}.csv")

        with open(file_path, "rb") as f:
            file_client.upload_data(f, overwrite=True)
        print(f"  ✓ Uploaded {name}.csv → OneLake Files/")


def run(client: FabricDeployClient, manifest: DeployManifest) -> None:
    """Execute the lakehouse stage: create, upload CSVs, load tables.

    Parameters:
        client: Authenticated Fabric REST client.
        manifest: Deploy manifest — ``lakehouse_id`` set on completion.

    Side effects:
        Creates Lakehouse, uploads files, loads delta tables.
        Mutates ``manifest.lakehouse_id``.
    """
    print("\n--- Stage 3: Lakehouse ---")

    if not manifest.schema_path or not Path(manifest.schema_path).exists():
        print(f"  ⚠ graph_schema.yaml not found: {manifest.schema_path}")
        print("    Skipping lakehouse stage")
        return

    tables = _load_table_list(manifest.schema_path)
    if not tables:
        print("  ⚠ No tables found in graph_schema.yaml — skipping")
        return

    print(f"  Tables to provision: {', '.join(tables)}")

    # Find or create lakehouse
    existing = client.find_item(
        manifest.workspace_id, "Lakehouse", manifest.lakehouse_name
    )

    if existing and manifest.force:
        print(f"  ⟳ Deleting existing Lakehouse: {existing['id']}...")
        client.delete_item(
            manifest.workspace_id, existing["id"], manifest.lakehouse_name
        )
        time.sleep(15)  # Wait for name release
        existing = None

    if existing:
        print(f"  ✓ Lakehouse already exists: {existing['id']}")
        manifest.lakehouse_id = existing["id"]
    else:
        print(f"  Creating Lakehouse: {manifest.lakehouse_name}...")
        result = client.create_item(
            workspace_id=manifest.workspace_id,
            item_type="Lakehouse",
            name=manifest.lakehouse_name,
            folder_id=manifest.folder_id or None,
            description=f"Entity data for {manifest.scenario}",
        )
        manifest.lakehouse_id = result["id"]
        print(f"  ✓ Lakehouse created: {manifest.lakehouse_id}")

    # Upload CSVs to OneLake
    print(f"\n  Uploading CSVs to OneLake...")
    if not client.dry_run:
        _upload_csvs(manifest, manifest.lakehouse_name, tables)
    else:
        for t in tables:
            print(f"  [DRY RUN] Would upload {t}.csv")

    # Load each CSV into a managed delta table
    print(f"\n  Loading delta tables...")
    for table_name in tables:
        relative_path = f"Files/{table_name}.csv"
        client.load_lakehouse_table(
            manifest.workspace_id, manifest.lakehouse_id, table_name, relative_path
        )
        print(f"  ✓ Loaded table: {table_name}")
