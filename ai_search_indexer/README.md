# AI Search Indexer

Manifest-driven deployment of Azure AI Search indexes with vector search and semantic ranking. Uploads knowledge docs to blob storage, creates chunking + embedding skillsets, and builds searchable indexes — all in one command.

## Pipeline

For each index declared in the manifest:

```
upload docs → blob data source → search index (HNSW + vectorizer) → skillset (chunk + embed) → indexer → poll
```

| Step | What it does |
|------|-------------|
| **Upload** | Uploads local markdown/text files to Azure Blob Storage containers |
| **Data source** | Creates a blob data source connection (RBAC-based, no keys) |
| **Index** | Creates a search index with vector search (HNSW) + Azure OpenAI vectorizer + semantic config |
| **Skillset** | Creates a SplitSkill (chunking) + AzureOpenAIEmbeddingSkill pipeline |
| **Indexer** | Creates and runs the indexer, polls until complete |

## Quick Start

### Prerequisites

```bash
pip install azure-search-documents azure-storage-blob azure-identity pyyaml
# Optional (for RBAC auto-setup):
pip install azure-mgmt-authorization azure-mgmt-search azure-mgmt-storage
az login
```

### Configure

```bash
cp .env.example azure_config.env
# Edit azure_config.env with your Azure resource names
```

Required env vars:

| Variable | Example | Purpose |
|----------|---------|---------|
| `AZURE_SUBSCRIPTION_ID` | `12345-abc-...` | Azure subscription |
| `AZURE_RESOURCE_GROUP` | `rg-myproject` | Resource group containing all resources |
| `AI_SEARCH_NAME` | `my-search-svc` | AI Search service name |
| `STORAGE_ACCOUNT_NAME` | `mystorage123` | Blob storage for knowledge docs |
| `AI_FOUNDRY_NAME` | `my-ai-foundry` | Azure OpenAI / AI Foundry for embeddings |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model deployment name |
| `EMBEDDING_DIMENSIONS` | `1536` | Vector dimensions |

### Deploy

```bash
cd scripts

# Dry run (preview only, no Azure credentials needed)
python3 deploy_scenario.py --manifest ../sample_scenario/search_manifest.yaml --dry-run

# Deploy with file upload
python3 deploy_scenario.py --manifest ../sample_scenario/search_manifest.yaml --upload-files

# Deploy without uploading (files already in blob storage)
python3 deploy_scenario.py --manifest ../sample_scenario/search_manifest.yaml
```

### Direct provisioner usage

```bash
# The provisioner can also be called directly (same flags):
python3 provision_search_index.py --manifest ../sample_scenario/search_manifest.yaml --upload-files --dry-run
```

## Sample Scenario

The `sample_scenario/` directory contains a minimal ops knowledge base:

```
sample_scenario/
├── search_manifest.yaml              ← Index definitions (3 indexes)
└── data/knowledge/
    ├── runbooks/                      ← Operational procedures (*.md)
    │   ├── server_restart_procedure.md
    │   ├── database_failover_runbook.md
    │   └── ssl_certificate_renewal.md
    ├── tickets/                       ← Incident history (*.txt)
    │   ├── INC-2025-11-14-0042.txt
    │   ├── INC-2025-12-03-0019.txt
    │   └── INC-2026-01-22-0008.txt
    └── equipment/                     ← Equipment manifests (*.md)
        ├── f5_loadbalancer_syd_manifest.md
        └── ups_apc_mel_manifest.md
```

## Creating Your Own Scenario

1. Organize your knowledge docs into subdirectories by type (e.g., `runbooks/`, `policies/`, `specs/`)
2. Create a `search_manifest.yaml`:

```yaml
scenario:
  name: "my-project"

indexes:
  - name: "my-project-policies-index"      # Index name in AI Search
    source: "data/knowledge/policies"       # Local directory (relative to manifest)
    file_types: ["*.md"]                    # File glob
    blob_container: "my-project-policies"   # Blob container name
    description: "Company policies"
```

3. Run: `python3 deploy_scenario.py --manifest path/to/search_manifest.yaml --upload-files`

## Pre-Flight Checks

The provisioner automatically handles common setup issues:

- **Storage public access**: Checks and enables if disabled (prevents AuthorizationFailure errors)
- **Search RBAC**: Assigns Storage Blob Data Reader to the Search service's managed identity
- **Blob containers**: Creates containers before indexer creation

## Architecture

```
scripts/
├── deploy_scenario.py           ← Entry point — loads manifest + env, calls provisioner
└── provision_search_index.py    ← Engine — blob upload, index, skillset, indexer (805 lines)
```

The provisioner is stateless and idempotent — safe to re-run. It deletes and recreates the indexer/skillset pipeline each time (field schema changes require recreation).

## Auth

Uses `DefaultAzureCredential` — works with:
- `az login` for local development
- Managed identity for deployed environments
- No API keys stored anywhere
