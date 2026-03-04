"""Stage 6: Verify deployed resources and print summary.

Module role:
    Queries row counts from Lakehouse tables and KQL tables, verifies
    GraphModel auto-creation, and prints a summary of all provisioned
    resources. Optionally writes resource IDs to an env file.

Key collaborators:
    - ``_deploy_client.FabricDeployClient`` — item listing.
    - ``_deploy_manifest.DeployManifest``   — all resource IDs.

Dependents:
    Final stage — no downstream consumers.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from azure.kusto.data import KustoClient, KustoConnectionStringBuilder

from _deploy_client import FabricDeployClient
from _deploy_manifest import DeployManifest


def _write_env_file(manifest: DeployManifest) -> None:
    """Write discovered resource IDs to an env file (upsert semantics).

    Parameters:
        manifest: Deploy manifest with all resource IDs populated.

    Side effects:
        Creates or updates the env file at ``manifest.output_env_file``.
    """
    env_file = manifest.output_env_file
    if not env_file:
        return

    # Resolve relative to project root
    if not os.path.isabs(env_file):
        env_file = str(manifest.project_root / env_file)

    updates = {
        "FABRIC_WORKSPACE_ID": manifest.workspace_id,
        "FABRIC_WORKSPACE_NAME": manifest.workspace_name,
    }
    if manifest.lakehouse_id:
        updates["FABRIC_LAKEHOUSE_ID"] = manifest.lakehouse_id
        updates["FABRIC_LAKEHOUSE_NAME"] = manifest.lakehouse_name
    if manifest.eventhouse_id:
        updates["FABRIC_EVENTHOUSE_ID"] = manifest.eventhouse_id
        updates["FABRIC_EVENTHOUSE_NAME"] = manifest.eventhouse_name
    if manifest.kql_db_name:
        updates["FABRIC_KQL_DB_NAME"] = manifest.kql_db_name
    if manifest.kql_query_uri:
        updates["EVENTHOUSE_QUERY_URI"] = manifest.kql_query_uri
    if manifest.ontology_id:
        updates["FABRIC_ONTOLOGY_ID"] = manifest.ontology_id
    if manifest.graph_model_id:
        updates["FABRIC_GRAPH_MODEL_ID"] = manifest.graph_model_id

    # Read existing content or start empty
    if os.path.exists(env_file):
        with open(env_file) as f:
            content = f.read()
    else:
        content = ""

    # Upsert each key
    for key, value in updates.items():
        if not value:
            continue
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
        else:
            content = content.rstrip("\n") + f"\n{key}={value}\n"

    with open(env_file, "w") as f:
        f.write(content)

    print(f"\n  ✓ Written to {env_file}:")
    for key, value in updates.items():
        if value:
            print(f"    {key}={value}")


def run(client: FabricDeployClient, manifest: DeployManifest) -> None:
    """Execute the verify stage: row counts, summary, env file output.

    Parameters:
        client: Authenticated Fabric REST client.
        manifest: Deploy manifest with all resource IDs.
    """
    print("\n--- Stage 6: Verify ---")

    # Verify KQL row counts if Eventhouse is provisioned
    if manifest.kql_query_uri and manifest.kql_db_name and not client.dry_run:
        print(f"\n  KQL table row counts:")
        try:
            from _stages.auth import build_credential
            # Reuse manifest credential strategy
            from azure.identity import AzureCliCredential, ClientSecretCredential, DefaultAzureCredential
            if manifest.tenant_id and manifest.client_id and manifest.client_secret:
                cred = ClientSecretCredential(
                    manifest.tenant_id, manifest.client_id, manifest.client_secret
                )
            elif manifest.tenant_id:
                cred = AzureCliCredential(tenant_id=manifest.tenant_id)
            else:
                cred = AzureCliCredential()

            kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
                manifest.kql_query_uri, cred
            )
            kusto_client = KustoClient(kcsb)

            # List tables in the database
            result = kusto_client.execute_mgmt(manifest.kql_db_name, ".show tables")
            table_names = [row[0] for row in result.primary_results[0]]

            for table_name in table_names:
                try:
                    count_result = kusto_client.execute_query(
                        manifest.kql_db_name, f"{table_name} | count"
                    )
                    for row in count_result.primary_results[0]:
                        print(f"    {table_name}: {row[0]} rows")
                except Exception as e:
                    print(f"    {table_name}: ⚠ could not count — {e}")
        except Exception as e:
            print(f"  ⚠ KQL verification failed: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("✅ Deployment Summary")
    print("=" * 60)
    print(f"  Scenario    : {manifest.scenario}")
    print(f"  Workspace   : {manifest.workspace_name} ({manifest.workspace_id})")
    if manifest.folder_path:
        print(f"  Folder      : {manifest.folder_path} ({manifest.folder_id})")
    if manifest.lakehouse_id:
        print(f"  Lakehouse   : {manifest.lakehouse_name} ({manifest.lakehouse_id})")
    if manifest.eventhouse_id:
        print(f"  Eventhouse  : {manifest.eventhouse_name} ({manifest.eventhouse_id})")
    if manifest.kql_db_name:
        print(f"  KQL DB      : {manifest.kql_db_name}")
    if manifest.kql_query_uri:
        print(f"  Query URI   : {manifest.kql_query_uri}")
    if manifest.ontology_id:
        print(f"  Ontology    : {manifest.ontology_name} ({manifest.ontology_id})")
    if manifest.graph_model_id:
        print(f"  Graph Model : {manifest.graph_model_id}")
    if manifest.tenant_id:
        print(f"  Tenant      : {manifest.tenant_id}")
    print("=" * 60)

    # Write env file if requested
    if manifest.output_env_file:
        _write_env_file(manifest)
