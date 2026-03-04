#!/usr/bin/env python3
"""
Provision AI Search indexes — manifest-driven pipeline.

Creates the full AI Search indexing pipeline per index entry in a manifest:
  blob data source → index (HNSW + vectorizer) → skillset → indexer

The manifest (search_manifest.yaml) is the single source of truth for what
indexes to create, what files to upload, and what blob containers to use.
No hardcoded index type keys — any scenario can declare any number of indexes.

Usage:
    # Manifest-driven (recommended):
    uv run python3 scripts/provision_search_index.py --manifest data/scenarios/airline-ops/search_manifest.yaml

    # With file upload:
    uv run python3 scripts/provision_search_index.py --manifest data/scenarios/airline-ops/search_manifest.yaml --upload-files

    # Dry run (preview only):
    uv run python3 scripts/provision_search_index.py --manifest data/scenarios/airline-ops/search_manifest.yaml --dry-run

    # Legacy mode (backward compat — reads from scenario_loader):
    DEFAULT_SCENARIO=telecom-playground uv run python3 scripts/provision_search_index.py --upload-files

Requires: azure-search-documents, azure-storage-blob, azure-identity, pyyaml
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Azure SDK imports are deferred to function scope so that --dry-run works
# without azure-search-documents installed. The actual imports happen inside
# the functions that need them (run(), _create_index(), etc.).

# ---------------------------------------------------------------------------
# Configuration — loaded at runtime, not module level
# ---------------------------------------------------------------------------

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Chunking parameters
CHUNK_LENGTH = 2000
CHUNK_OVERLAP = 500


def _env(key: str, default: str = "") -> str:
    """Read an env var at call time (not import time).

    This ensures env vars loaded by deploy_scenario.py's _load_env_file()
    are visible even when this module was imported before the env file
    was parsed.
    """
    return os.environ.get(key, default)


def _build_configs_from_manifest(manifest_path: Path) -> dict[str, dict]:
    """Build INDEX_CONFIGS from a search_manifest.yaml file.

    Each entry in the manifest's ``indexes:`` list becomes one config dict
    with keys: blob_container, local_dir, file_glob, description,
    semantic_config_name. Paths are resolved relative to the manifest's
    parent directory.

    Args:
        manifest_path: Absolute path to search_manifest.yaml.

    Returns:
        Dict mapping index name → config dict.
    """
    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)

    # Source paths in the manifest are relative to the manifest's parent dir
    # (which is the scenario root, e.g. data/scenarios/airline-ops/)
    base_dir = manifest_path.parent

    configs: dict[str, dict] = {}
    for idx in manifest.get("indexes", []):
        name = idx["name"]
        source_dir = base_dir / idx["source"]
        # First file_types entry as glob; default to *.md
        file_types = idx.get("file_types", ["*.md"])
        file_glob = file_types[0] if file_types else "*.md"
        container = idx["blob_container"]

        if not source_dir.is_dir():
            print(f"  ⚠ Skipping '{name}': source dir not found: {source_dir}")
            continue

        configs[name] = {
            "blob_container": container,
            "local_dir": source_dir,
            "file_glob": file_glob,
            "description": idx.get("description", f"Knowledge index: {name}"),
            "semantic_config_name": f"{container}-semantic",
        }

    return configs


def _build_configs_legacy() -> dict[str, dict]:
    """Build INDEX_CONFIGS from scenario_loader (backward compat).

    Falls back to the old hardcoded runbooks/tickets/equipment/infra_specs
    pattern. Prints a deprecation warning.
    """
    print("  ⚠ DEPRECATED: Using scenario_loader fallback.")
    print("    Prefer: --manifest path/to/search_manifest.yaml")
    print()

    from scenario_loader import load_scenario
    sc = load_scenario()

    configs: dict[str, dict] = {}

    # Only add entries whose paths exist — no more KeyError crashes
    for key, glob_pattern, description in [
        ("runbooks", "*.md", "Operational runbooks"),
        ("tickets", "*.txt", "Historical incident tickets"),
    ]:
        if key in sc.get("paths", {}):
            configs[sc[f"{key}_index_name"]] = {
                "blob_container": sc[f"{key}_blob_container"],
                "local_dir": sc["paths"][key],
                "file_glob": glob_pattern,
                "description": description,
                "semantic_config_name": f"{sc[f'{key}_blob_container']}-semantic",
            }

    # Optional v2 indexes — check directory existence
    knowledge_dir = None
    if "runbooks" in sc.get("paths", {}):
        knowledge_dir = sc["paths"]["runbooks"].parent
    elif "procedures" in sc.get("paths", {}):
        knowledge_dir = sc["paths"]["procedures"].parent

    if knowledge_dir:
        for subdir, key, glob_pattern, description in [
            ("equipment", "equipment", "*.md", "Equipment manifests"),
            ("infra_specs", "infra_specs", "*.md", "Infrastructure specifications"),
        ]:
            d = knowledge_dir / subdir
            if d.is_dir():
                configs[sc[f"{key}_index_name"]] = {
                    "blob_container": sc[f"{key}_blob_container"],
                    "local_dir": d,
                    "file_glob": glob_pattern,
                    "description": description,
                    "semantic_config_name": f"{sc[f'{key}_blob_container']}-semantic",
                }

    return configs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_search_endpoint() -> str:
    return f"https://{_env('AI_SEARCH_NAME')}.search.windows.net"


def _get_storage_connection_string_resource_id() -> str:
    """Build ARM resource ID for storage account (used as data source connection)."""
    return (
        f"/subscriptions/{_env('AZURE_SUBSCRIPTION_ID')}"
        f"/resourceGroups/{_env('AZURE_RESOURCE_GROUP')}"
        f"/providers/Microsoft.Storage"
        f"/storageAccounts/{_env('STORAGE_ACCOUNT_NAME')}"
    )


def _get_ai_services_resource_id() -> str:
    """Build ARM resource ID for AI Foundry (used for vectorizer)."""
    return (
        f"/subscriptions/{_env('AZURE_SUBSCRIPTION_ID')}"
        f"/resourceGroups/{_env('AZURE_RESOURCE_GROUP')}"
        f"/providers/Microsoft.CognitiveServices"
        f"/accounts/{_env('AI_FOUNDRY_NAME')}"
    )


def _ensure_storage_public_access(credential: DefaultAzureCredential) -> None:
    """Check and enable public network access on the storage account if disabled.

    When publicNetworkAccess is Disabled, all data-plane requests from outside
    Azure private networking are rejected before RBAC is evaluated — causing
    AuthorizationFailure errors that look like missing role assignments.

    This pre-flight check catches the issue early and auto-enables access.
    """
    try:
        from azure.mgmt.storage import StorageManagementClient

        sub_id = _env('AZURE_SUBSCRIPTION_ID')
        rg = _env('AZURE_RESOURCE_GROUP')
        acct = _env('STORAGE_ACCOUNT_NAME')

        mgmt = StorageManagementClient(credential, sub_id)
        props = mgmt.storage_accounts.get_properties(rg, acct)

        if props.public_network_access and props.public_network_access.lower() == "disabled":
            print("  ⚠ Storage account has publicNetworkAccess=Disabled — enabling...")
            from azure.mgmt.storage.models import StorageAccountUpdateParameters
            mgmt.storage_accounts.update(
                rg, acct,
                StorageAccountUpdateParameters(public_network_access="Enabled"),
            )
            print("  ✓ Public network access enabled on storage account")
            # Brief wait for propagation
            import time
            time.sleep(5)
        else:
            print("  ✓ Storage public network access: OK")
    except ImportError:
        print("  ⚠ azure-mgmt-storage not installed — skipping public access check")
    except Exception as e:
        print(f"  ⚠ Could not check storage public access: {e}")


def _ensure_search_storage_rbac(credential: DefaultAzureCredential) -> None:
    """Ensure the AI Search managed identity has Storage Blob Data Reader on the storage account.

    The Search service indexer uses its system-assigned managed identity to read
    blobs from the storage account. Without this role assignment, the indexer
    can create a data source but fails at runtime with "Unable to retrieve blob
    container" — which looks like a missing container but is actually an auth failure.

    Auto-assigns the role if missing. Idempotent — succeeds silently if already assigned.

    Args:
        credential: DefaultAzureCredential for Azure management plane calls.

    Side effects:
        Creates an Azure role assignment if one does not exist.
        Waits 10 seconds after new assignment for RBAC propagation.
    """
    try:
        from azure.mgmt.authorization import AuthorizationManagementClient
        from azure.mgmt.search import SearchManagementClient

        sub_id = _env('AZURE_SUBSCRIPTION_ID')
        rg = _env('AZURE_RESOURCE_GROUP')
        search_name = _env('AI_SEARCH_NAME')
        storage_name = _env('STORAGE_ACCOUNT_NAME')

        # Resolve Search service managed identity principal ID
        search_mgmt = SearchManagementClient(credential, sub_id)
        search_svc = search_mgmt.services.get(rg, search_name)
        search_principal_id = getattr(search_svc.identity, 'principal_id', None) if search_svc.identity else None

        if not search_principal_id:
            print("  ⚠ Search service has no managed identity — skipping RBAC check")
            print("    Enable system-assigned MI on the Search service and re-run.")
            return

        # Storage Blob Data Reader role definition ID (built-in, same across all tenants)
        blob_reader_role = "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1"
        storage_scope = (
            f"/subscriptions/{sub_id}/resourceGroups/{rg}"
            f"/providers/Microsoft.Storage/storageAccounts/{storage_name}"
        )

        # Check if the role assignment already exists
        auth_mgmt = AuthorizationManagementClient(credential, sub_id)
        existing = list(auth_mgmt.role_assignments.list_for_scope(
            storage_scope,
            filter=f"principalId eq '{search_principal_id}'",
        ))

        # Check if Storage Blob Data Reader is among the existing assignments
        has_reader = any(
            ra.role_definition_id and blob_reader_role in ra.role_definition_id
            for ra in existing
        )

        if has_reader:
            print("  ✓ Search MI → Storage Blob Data Reader: OK")
            return

        # Assign the role
        import uuid
        assignment_name = str(uuid.uuid4())
        auth_mgmt.role_assignments.create(
            storage_scope,
            assignment_name,
            {
                "role_definition_id": f"/subscriptions/{sub_id}/providers/Microsoft.Authorization/roleDefinitions/{blob_reader_role}",
                "principal_id": search_principal_id,
                "principal_type": "ServicePrincipal",
            },
        )
        print("  ✓ Assigned Storage Blob Data Reader to Search MI")
        print("    Waiting 10s for RBAC propagation...")
        time.sleep(10)

    except ImportError:
        print("  ⚠ azure-mgmt-authorization or azure-mgmt-search not installed — skipping RBAC check")
        print("    Install: pip install azure-mgmt-authorization azure-mgmt-search")
    except Exception as e:
        print(f"  ⚠ Could not check/assign Search RBAC: {e}")
        print(f"    Manually run: az role assignment create --assignee-object-id <SEARCH_MI_OID> "
              f"--role 'Storage Blob Data Reader' --scope <STORAGE_ID>")


def _ensure_blob_containers(credential: DefaultAzureCredential, index_configs: dict) -> None:
    """Ensure all blob containers referenced by index configs exist.

    Creates containers that don't exist. Idempotent — succeeds silently
    if the container already exists. This pre-flight step prevents indexer
    failures when --upload-files is not used (containers would otherwise
    only be created during the upload step).

    Args:
        credential: DefaultAzureCredential for Azure data plane calls.
        index_configs: Dict mapping index name → config dict with blob_container key.

    Side effects:
        Creates blob containers on the storage account if they don't exist.
    """
    from azure.storage.blob import BlobServiceClient

    blob_url = f"https://{_env('STORAGE_ACCOUNT_NAME')}.blob.core.windows.net"
    blob_client = BlobServiceClient(blob_url, credential=credential)

    # Collect unique container names from all index configs
    containers = {cfg["blob_container"] for cfg in index_configs.values()}

    for container_name in sorted(containers):
        try:
            blob_client.create_container(container_name)
            print(f"  ✓ Created container: {container_name}")
        except Exception:
            # Container already exists — no action needed
            pass


def _upload_files_to_blob(credential: DefaultAzureCredential, config: dict, index_name: str) -> int:
    """Upload local files to blob storage container. Returns file count."""
    from azure.storage.blob import BlobServiceClient

    blob_url = f"https://{_env('STORAGE_ACCOUNT_NAME')}.blob.core.windows.net"
    blob_client = BlobServiceClient(blob_url, credential=credential)

    container_name = config["blob_container"]
    local_dir = config["local_dir"]
    file_glob = config["file_glob"]

    # Ensure container exists
    try:
        blob_client.create_container(container_name)
        print(f"    Created blob container: {container_name}")
    except Exception:
        print(f"    Blob container exists: {container_name}")

    # Upload files
    files = list(local_dir.glob(file_glob))
    if not files:
        print(f"    ✗ No files matching {file_glob} in {local_dir}")
        return 0

    container_client = blob_client.get_container_client(container_name)
    uploaded = 0
    for f in files:
        blob_name = f.name
        with open(f, "rb") as data:
            container_client.upload_blob(name=blob_name, data=data, overwrite=True)
            uploaded += 1
    print(f"    ✓ Uploaded {uploaded} files to '{container_name}' container")
    return uploaded


def _create_data_source(
    indexer_client,
    index_name: str,
    config: dict,
) -> str:
    """Create or update blob data source connection. Returns data source name."""
    from azure.search.documents.indexes.models import (
        SearchIndexerDataSourceConnection, SearchIndexerDataContainer,
    )

    ds_name = f"{index_name}-datasource"
    resource_id = _get_storage_connection_string_resource_id()

    data_source = SearchIndexerDataSourceConnection(
        name=ds_name,
        type="azureblob",
        connection_string=f"ResourceId={resource_id};",
        container=SearchIndexerDataContainer(name=config["blob_container"]),
    )

    indexer_client.create_or_update_data_source_connection(data_source)
    print(f"    ✓ Data source: {ds_name}")
    return ds_name


def _create_index(
    index_client,
    index_name: str,
    config: dict,
) -> None:
    """Create or update search index with vector search and semantic config."""
    from azure.search.documents.indexes.models import (
        SearchIndex, SearchField, SearchFieldDataType, SearchableField, SimpleField,
        VectorSearch, HnswAlgorithmConfiguration, VectorSearchProfile,
        AzureOpenAIVectorizer, AzureOpenAIVectorizerParameters,
        SemanticConfiguration, SemanticSearch, SemanticPrioritizedFields, SemanticField,
    )

    # Delete existing index if it exists (field schema changes require recreation)
    try:
        index_client.delete_index(index_name)
        print(f"    Deleted existing index '{index_name}' (field changes require recreation)")
    except Exception:
        pass  # Index doesn't exist yet — that's fine

    fields = [
        SearchField(name="chunk_id", type=SearchFieldDataType.String, key=True, filterable=True, analyzer_name="keyword"),
        SimpleField(name="parent_id", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="chunk", type=SearchFieldDataType.String),
        SearchableField(name="title", type=SearchFieldDataType.String, filterable=True),
        SearchField(
            name="text_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=int(_env('EMBEDDING_DIMENSIONS', '1536')),
            vector_search_profile_name="hnsw-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(name="hnsw-config"),
        ],
        profiles=[
            VectorSearchProfile(
                name="hnsw-profile",
                algorithm_configuration_name="hnsw-config",
                vectorizer_name="openai-vectorizer",
            ),
        ],
        vectorizers=[
            AzureOpenAIVectorizer(
                vectorizer_name="openai-vectorizer",
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=f"https://{_env('AI_FOUNDRY_NAME')}.openai.azure.com",
                    deployment_name=_env('EMBEDDING_MODEL', 'text-embedding-3-small'),
                    model_name=_env('EMBEDDING_MODEL', 'text-embedding-3-small'),
                ),
            ),
        ],
    )

    semantic_config = SemanticConfiguration(
        name=config["semantic_config_name"],
        prioritized_fields=SemanticPrioritizedFields(
            content_fields=[SemanticField(field_name="chunk")],
            title_field=SemanticField(field_name="title"),
        ),
    )

    index = SearchIndex(
        name=index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=SemanticSearch(configurations=[semantic_config]),
    )

    index_client.create_or_update_index(index)
    print(f"    ✓ Index: {index_name}")


def _create_skillset(
    indexer_client,
    index_name: str,
) -> str:
    """Create or update skillset with SplitSkill + AzureOpenAIEmbeddingSkill."""
    from azure.search.documents.indexes.models import (
        SearchIndexerSkillset, SplitSkill, AzureOpenAIEmbeddingSkill,
        InputFieldMappingEntry, OutputFieldMappingEntry,
        SearchIndexerIndexProjection, SearchIndexerIndexProjectionSelector,
        SearchIndexerIndexProjectionsParameters,
    )

    skillset_name = f"{index_name}-skillset"
    ai_resource_id = _get_ai_services_resource_id()

    split_skill = SplitSkill(
        name="split-skill",
        description="Split documents into chunks",
        text_split_mode="pages",
        context="/document",
        maximum_page_length=CHUNK_LENGTH,
        page_overlap_length=CHUNK_OVERLAP,
        inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
        outputs=[OutputFieldMappingEntry(name="textItems", target_name="pages")],
    )

    embedding_skill = AzureOpenAIEmbeddingSkill(
        name="embedding-skill",
        description="Generate embeddings for chunks",
        context="/document/pages/*",
        resource_url=f"https://{_env('AI_FOUNDRY_NAME')}.openai.azure.com",
        deployment_name=_env('EMBEDDING_MODEL', 'text-embedding-3-small'),
        model_name=_env('EMBEDDING_MODEL', 'text-embedding-3-small'),
        dimensions=int(_env('EMBEDDING_DIMENSIONS', '1536')),
        inputs=[InputFieldMappingEntry(name="text", source="/document/pages/*")],
        outputs=[OutputFieldMappingEntry(name="embedding", target_name="text_vector")],
    )

    # Index projection: project chunks into the target index
    index_projection = SearchIndexerIndexProjection(
        selectors=[
            SearchIndexerIndexProjectionSelector(
                target_index_name=index_name,
                parent_key_field_name="parent_id",
                source_context="/document/pages/*",
                mappings=[
                    InputFieldMappingEntry(name="chunk", source="/document/pages/*"),
                    InputFieldMappingEntry(name="text_vector", source="/document/pages/*/text_vector"),
                    InputFieldMappingEntry(name="title", source="/document/metadata_storage_name"),
                ],
            ),
        ],
        parameters=SearchIndexerIndexProjectionsParameters(
            projection_mode="skipIndexingParentDocuments"
        ),
    )

    skillset = SearchIndexerSkillset(
        name=skillset_name,
        description=f"Chunking and embedding for {index_name}",
        skills=[split_skill, embedding_skill],
        index_projection=index_projection,
    )

    indexer_client.create_or_update_skillset(skillset)
    print(f"    ✓ Skillset: {skillset_name}")
    return skillset_name


def _create_indexer(
    indexer_client,
    index_name: str,
    data_source_name: str,
    skillset_name: str,
) -> str:
    """Create or update indexer and run it."""
    from azure.search.documents.indexes.models import SearchIndexer

    indexer_name = f"{index_name}-indexer"

    indexer = SearchIndexer(
        name=indexer_name,
        description=f"Indexer for {index_name}",
        data_source_name=data_source_name,
        skillset_name=skillset_name,
        target_index_name=index_name,
    )

    indexer_client.create_or_update_indexer(indexer)
    print(f"    ✓ Indexer: {indexer_name}")
    return indexer_name


def _poll_indexer(
    indexer_client,
    indexer_name: str,
    timeout_seconds: int = 300,
) -> bool:
    """Poll indexer status until complete or timeout. Returns True if successful."""
    print(f"    Polling indexer '{indexer_name}'...")

    # Reset / run the indexer (retry if another invocation is in progress)
    indexer_started = False
    for attempt in range(1, 7):
        try:
            indexer_client.run_indexer(indexer_name)
            indexer_started = True
            break
        except Exception as e:
            if "concurrent" in str(e).lower() or "another indexer" in str(e).lower():
                print(f"    ⏳ Indexer busy (attempt {attempt}/6), waiting 15s...")
                time.sleep(15)
            else:
                print(f"    ⚠ Could not start indexer: {e}")
                break

    if not indexer_started:
        print("    ⚠ Proceeding — will poll for existing run to complete")

    start = time.time()
    while time.time() - start < timeout_seconds:
        time.sleep(5)
        try:
            status = indexer_client.get_indexer_status(indexer_name)
            last_result = status.last_result
            if last_result is None:
                print("    ... waiting for first run...")
                continue

            exec_status = last_result.status
            if exec_status == "success":
                doc_count = last_result.item_count or 0
                print(f"    ✓ Indexer complete: {doc_count} documents indexed")
                return True
            elif exec_status == "transientFailure":
                print(f"    ⚠ Transient failure, retrying...")
                continue
            elif exec_status == "persistentFailure":
                error_msg = last_result.errors[0].message if last_result.errors else "unknown"
                print(f"    ✗ Persistent failure: {error_msg[:200]}")
                return False
            else:
                print(f"    ... status: {exec_status}")
        except Exception as e:
            print(f"    ⚠ Status check error: {e}")

    print(f"    ✗ Timeout after {timeout_seconds}s")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace, index_configs: dict[str, dict]) -> None:
    """Provision AI Search indexes from the provided config dict.

    Args:
        args: Parsed CLI arguments (upload_files, dry_run).
        index_configs: Dict mapping index name → config dict with keys:
            blob_container, local_dir, file_glob, description, semantic_config_name.
    """
    print("=" * 72)
    print("  AI Search — Index Provisioner")
    print("=" * 72)

    if not index_configs:
        print("\n  ✗ No indexes to create. Check your manifest or scenario config.")
        sys.exit(1)

    # Show plan first (visible in dry-run even without env vars)
    print(f"\n  Indexes to create: {len(index_configs)}")
    for name, cfg in index_configs.items():
        file_count = len(list(cfg["local_dir"].glob(cfg["file_glob"])))
        print(f"    • {name}")
        print(f"      container: {cfg['blob_container']}  |  files: {file_count}  |  source: {cfg['local_dir']}")

    # Dry-run: print plan and exit (no Azure credentials needed)
    if getattr(args, "dry_run", False):
        print(f"\n  [DRY RUN] Would create {len(index_configs)} index(es). No changes made.")
        return

    # Validate Azure config (only needed for real execution)
    missing = []
    if not _env('AI_SEARCH_NAME'):
        missing.append("AI_SEARCH_NAME")
    if not _env('STORAGE_ACCOUNT_NAME'):
        missing.append("STORAGE_ACCOUNT_NAME")
    if not _env('AI_FOUNDRY_NAME'):
        missing.append("AI_FOUNDRY_NAME")
    if not _env('AZURE_SUBSCRIPTION_ID'):
        missing.append("AZURE_SUBSCRIPTION_ID")
    if not _env('AZURE_RESOURCE_GROUP'):
        missing.append("AZURE_RESOURCE_GROUP")
    if missing:
        print(f"\n  ✗ Missing required env vars: {', '.join(missing)}")
        print("    Set them in azure_config.env or export them before running.")
        sys.exit(1)

    endpoint = _get_search_endpoint()
    print(f"\n  Search endpoint:   {endpoint}")
    print(f"  Storage account:   {_env('STORAGE_ACCOUNT_NAME')}")
    print(f"  Embedding model:   {_env('EMBEDDING_MODEL', 'text-embedding-3-small')} ({int(_env('EMBEDDING_DIMENSIONS', '1536'))}d)")
    print(f"  AI Foundry:        {_env('AI_FOUNDRY_NAME')}")

    # Lazy-import Azure SDKs (deferred so --dry-run works without them)
    from azure.identity import DefaultAzureCredential
    from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient

    credential = DefaultAzureCredential()

    # Pre-flight checks — fix common issues before indexer creation
    _ensure_storage_public_access(credential)
    _ensure_search_storage_rbac(credential)
    _ensure_blob_containers(credential, index_configs)

    index_client = SearchIndexClient(endpoint=endpoint, credential=credential)
    indexer_client = SearchIndexerClient(endpoint=endpoint, credential=credential)

    total_indexes = len(index_configs)
    success_count = 0

    for idx, (index_name, config) in enumerate(index_configs.items(), 1):
        print(f"\n[{idx}/{total_indexes}] {index_name}")
        print(f"  {'-' * 50}")

        try:
            # Clean up existing pipeline (indexer → skillset → index must be deleted in order)
            for resource_name, delete_fn in [
                (f"{index_name}-indexer", indexer_client.delete_indexer),
                (f"{index_name}-skillset", indexer_client.delete_skillset),
            ]:
                try:
                    delete_fn(resource_name)
                except Exception:
                    pass

            # Optional: upload files to blob storage
            if args.upload_files:
                print("  Uploading files to blob storage...")
                _upload_files_to_blob(credential, config, index_name)

            # Create data source
            print("  Creating data source...")
            ds_name = _create_data_source(indexer_client, index_name, config)

            # Create index
            print("  Creating index...")
            _create_index(index_client, index_name, config)

            # Create skillset
            print("  Creating skillset...")
            skillset_name = _create_skillset(indexer_client, index_name)

            # Create indexer
            print("  Creating indexer...")
            indexer_name = _create_indexer(indexer_client, index_name, ds_name, skillset_name)

            # Poll for completion
            if _poll_indexer(indexer_client, indexer_name):
                success_count += 1
            else:
                print(f"  ⚠ Index '{index_name}' created but indexer did not complete successfully")
                success_count += 1  # Index exists, just indexer hasn't finished

        except Exception as e:
            print(f"  ✗ Failed to create {index_name}: {e}")

    # Summary
    print(f"\n{'=' * 72}")
    if success_count == total_indexes:
        print(f"  ✅ All {total_indexes} indexes created successfully")
    else:
        print(f"  ⚠ {success_count}/{total_indexes} indexes created")
    print("=" * 72)
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Create AI Search indexes with vector search and semantic ranking",
    )
    parser.add_argument(
        "--manifest", metavar="FILE",
        help="Path to search_manifest.yaml (recommended). "
             "Without this flag, falls back to scenario_loader (deprecated).",
    )
    parser.add_argument(
        "--upload-files",
        action="store_true",
        help="Upload local files to blob containers before creating indexes",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created/deleted without making changes",
    )
    args = parser.parse_args()

    # Build index configs from manifest or legacy loader
    if args.manifest:
        manifest_path = Path(args.manifest).resolve()
        if not manifest_path.exists():
            print(f"ERROR: Manifest not found: {manifest_path}")
            sys.exit(1)
        print(f"  Manifest: {manifest_path}")
        index_configs = _build_configs_from_manifest(manifest_path)
    else:
        index_configs = _build_configs_legacy()

    run(args, index_configs)


if __name__ == "__main__":
    main()
