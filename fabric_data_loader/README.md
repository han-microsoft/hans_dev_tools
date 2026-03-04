# Fabric Data Loader

Single-command deployment of graph scenarios to Microsoft Fabric. Creates workspace resources (Lakehouse, Eventhouse, Graph Ontology), uploads entity CSVs, ingests telemetry, and writes discovered resource IDs back to an env file.

## Pipeline Stages

```
auth → workspace → folder → lakehouse → eventhouse → ontology → verify
```

| Stage | What it does |
|-------|-------------|
| **auth** | Builds credential (AzureCliCredential or DefaultAzureCredential) |
| **workspace** | Finds or creates Fabric workspace, assigns capacity |
| **folder** | Creates workspace folder tree for resource organization |
| **lakehouse** | Creates Lakehouse, uploads entity CSVs via OneLake ADLS Gen2, loads delta tables |
| **eventhouse** | Creates Eventhouse + KQL database, builds table schemas from CSVs, ingests telemetry |
| **ontology** | Generates Fabric Graph ontology from `graph_schema.yaml`, creates entity/relationship types |
| **verify** | Validates deployed resources, prints summary, writes env file |

## Quick Start

### Prerequisites

```bash
pip install azure-identity azure-storage-file-datalake azure-kusto-data azure-kusto-ingest pyyaml requests python-dotenv
az login  # Authenticate to your Azure tenant
```

### Deploy from manifest

```bash
cd scripts
python3 deploy_scenario.py --manifest ../sample_scenario/deploy_manifest.yaml
```

### Deploy with CLI args

```bash
cd scripts
python3 deploy_scenario.py \
    --scenario sample-bookstore \
    --data-root ../sample_scenario \
    --workspace-name MyGraphWorkspace \
    --capacity-id <your-capacity-id> \
    --tenant-id <your-tenant-id> \
    --output-env azure_config.env
```

### Skip stages or resume

```bash
# Skip workspace and folder (already exist)
python3 deploy_scenario.py --manifest ../sample_scenario/deploy_manifest.yaml --skip workspace,folder

# Run only lakehouse and eventhouse
python3 deploy_scenario.py --manifest ../sample_scenario/deploy_manifest.yaml --only lakehouse,eventhouse
```

## Sample Scenario

The `sample_scenario/` directory contains a minimal bookstore graph:

```
sample_scenario/
├── deploy_manifest.yaml     ← Deployment config (edit with your tenant/workspace)
├── graph_schema.yaml        ← Ontology definition (vertices + edges)
├── scenario.yaml            ← Telemetry container config
└── data/
    ├── entities/            ← Graph entity CSVs → Lakehouse delta tables
    │   ├── Authors.csv      (5 authors)
    │   ├── Books.csv        (10 books)
    │   ├── Publishers.csv   (5 publishers)
    │   ├── Wrote.csv        (10 author→book edges)
    │   └── PublishedBy.csv  (10 book→publisher edges)
    └── telemetry/           ← Time-series CSVs → Eventhouse KQL tables
        ├── BookEvents.csv   (page views, purchases)
        └── BookReviews.csv  (ratings, review text)
```

## Creating Your Own Scenario

1. **Entity CSVs** — one CSV per vertex/edge type, first row = header, key column matches `graph_schema.yaml`
2. **`graph_schema.yaml`** — declares vertices (with properties + key) and edges (with source/target vertex types)
3. **Telemetry CSVs** — time-series data, one CSV per KQL table
4. **`scenario.yaml`** — declares telemetry containers with column types
5. **`deploy_manifest.yaml`** — points at your data, names your Fabric resources

## Architecture

```
scripts/
├── deploy_scenario.py   ← Entry point — orchestrates all stages
├── _deploy_client.py    ← Authenticated Fabric REST client (756 lines)
├── _deploy_manifest.py  ← Typed config from YAML or CLI args
├── _config.py           ← Shared env loading (legacy scripts)
└── _stages/
    ├── auth.py          ← Credential builder
    ├── workspace.py     ← Find/create workspace
    ├── folder.py        ← Create folder tree
    ├── lakehouse.py     ← Create Lakehouse + upload + delta tables
    ├── eventhouse.py    ← Create Eventhouse + KQL schemas + ingest
    ├── ontology.py      ← Generate + deploy Graph ontology
    └── verify.py        ← Validate + write env file
```

Each stage is a composable function: `run(client: FabricDeployClient, manifest: DeployManifest) → None`. Stages mutate the manifest with discovered resource IDs so downstream stages can reference them.
