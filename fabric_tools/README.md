# Fabric Tools — Standalone Query Tools for Microsoft Fabric

Self-contained Python tools for querying Microsoft Fabric data:
- **Graph Explorer** — Query network topology using GQL against Fabric Graph Model
- **Telemetry Analyzer** — Query performance metrics using KQL against Fabric Eventhouse
- **Alert Monitor** — Query network alerts using KQL
- **Ontology Discovery** — Auto-detect vertex/edge types from the graph model

Extracted from [PathfinderIQ](https://github.com/hanchoong/pathfinderiq_azure_native_agentic_graphs). Includes production-grade resilience: circuit breaker, concurrency control, cold-start retry, 429 backoff, read-only guardrails, and token caching.

---

## Prerequisites

1. **Python 3.11+** installed
2. **[uv](https://docs.astral.sh/uv/)** package manager — install with:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. **Azure CLI** logged in — `az login` (for local development auth)
4. **Microsoft Fabric workspace** with:
   - A **Graph Model** (ontology) containing your data
   - An **Eventhouse** with KQL database containing telemetry/alert tables
5. **Service Principal** with access to the Fabric workspace (if cross-tenant)

---

## Step-by-Step Setup

### 1. Install dependencies

```bash
cd fabric_tools
uv sync
```

This creates a `.venv/` folder and installs all required packages.

### 2. Configure credentials

```bash
cp .env.example .env
```

Open `.env` in a text editor and fill in your values:

```bash
# Required: Fabric Service Principal (ask your Fabric admin)
FABRIC_TENANT_ID=your-entra-tenant-id
FABRIC_CLIENT_ID=your-app-registration-client-id
FABRIC_CLIENT_SECRET=your-client-secret

# Required: Your Fabric workspace and graph model IDs
# Find these in the Fabric portal URL when viewing your workspace/item
FABRIC_WORKSPACE_ID=your-workspace-guid
FABRIC_GRAPH_MODEL_ID=your-graph-model-guid

# Required: Your Eventhouse query endpoint and database name
# Find the query URI in Eventhouse → Query URI (copy button)
EVENTHOUSE_QUERY_URI=https://your-cluster.kusto.fabric.microsoft.com
FABRIC_KQL_DB_NAME=your-kql-database-name
```

**How to find these values:**
- **FABRIC_WORKSPACE_ID**: Open your Fabric workspace in the browser → the GUID is in the URL after `/workspaces/`
- **FABRIC_GRAPH_MODEL_ID**: Open your Graph Model item → the GUID is in the URL after `/GraphModels/` or in the item details
- **EVENTHOUSE_QUERY_URI**: Open your Eventhouse → click "Query URI" copy button
- **FABRIC_KQL_DB_NAME**: The name of your KQL database (visible in the Eventhouse)
- **Service Principal**: Created via Azure Portal → Entra ID → App registrations. Must be added as a Member in the Fabric workspace. See [PathfinderIQ README](https://github.com/hanchoong/pathfinderiq_azure_native_agentic_graphs#cross-tenant-fabric-setup) for full setup guide.

### 3. Run the tests

```bash
uv run python3 test_tools.py
```

**What it does** (no agent needed, no Azure AI Foundry required):

| Test | What it proves |
|------|---------------|
| 0. Ontology Discovery | Fetches all vertex/edge types from your graph model |
| 1. Graph Query | `MATCH (n:CoreRouter) RETURN n LIMIT 5` — lists vertices |
| 2. Edge Traversal | `MATCH (tl:TransportLink)-[r:connects_to]->(cr:CoreRouter)` — follows edges |
| 3. Alert Query | KQL query against your alert table |
| 4. Telemetry Query | KQL query against your telemetry table |
| 5. Mutation Guard | Verifies write queries are blocked before execution |
| 6. Auto-LIMIT | Verifies unbounded queries get a safety limit injected |

**Expected output** (all ✓):
```
▶ TEST 0: Ontology Discovery
  ✓ Node types: 14
  ✓ Edge types: 18
▶ TEST 1: Graph Query (GQL) — List CoreRouters
  ✓ Got 3 routers
▶ TEST 2: Graph Query (GQL) — Traverse edges
  ✓ Got 3 link→router connections
▶ TEST 3: Telemetry Query (KQL) — Recent alerts
  ✓ Got 5 alerts
...
```

### 4. (Optional) Test with an AI agent

This requires [Azure AI Foundry](https://ai.azure.com) with a deployed model.

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://your-foundry.services.ai.azure.com/api/projects/your-project"
export AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME="gpt-4.1"

uv run python3 test_with_agent.py
```

The agent receives the tools and decides when to call them based on your questions.

---

## How to Use in Your Code

### Basic: Query tools directly

```python
import asyncio
from dotenv import load_dotenv
load_dotenv()  # Load .env file

from fabric_tools import query_graph, query_telemetry, query_alerts, get_ontology

async def main():
    # 1. Discover the graph schema (do this once at startup)
    ontology = await get_ontology()
    print(ontology["summary"])  # Human-readable ontology for agent prompts

    # 2. Query the graph (ISO GQL, NOT Gremlin)
    result = await query_graph(query="MATCH (n:CoreRouter) RETURN n LIMIT 5")
    
    # 3. Query telemetry (KQL)
    result = await query_telemetry(query="AlertStream | take 5")

asyncio.run(main())
```

### With an Azure AI Foundry Agent

```python
from agent_framework.azure import AzureAIAgentClient
from azure.identity import DefaultAzureCredential
from fabric_tools import query_graph, query_telemetry, query_alerts, get_ontology

# Fetch ontology for the agent's instructions
ontology = asyncio.run(get_ontology())

client = AzureAIAgentClient(
    project_endpoint="https://your-foundry...",
    model_deployment_name="gpt-4.1",
    credential=DefaultAzureCredential(),
)

agent = client.as_agent(
    name="NetworkAnalyst",
    instructions=f"You analyze networks using graph and telemetry data.\n\n{ontology['summary']}",
    tools=[query_graph, query_telemetry, query_alerts],
)
```

---

## Query Syntax

### Graph (GQL — ISO GQL, NOT Gremlin)

```sql
-- List vertices
MATCH (n:CoreRouter) RETURN n LIMIT 10

-- Traverse edges
MATCH (tl:TransportLink)-[r:connects_to]->(cr:CoreRouter)
RETURN tl, cr LIMIT 10

-- Filter
MATCH (n:Service) WHERE n.ServiceType = 'Enterprise'
RETURN n LIMIT 10
```

**Important**: `RETURN n` returns vertex IDs. The Fabric GQL API currently returns IDs for vertex references — full property access via `n.PropertyName` may vary by graph model version.

### Telemetry (KQL)

```sql
-- Recent alerts
AlertStream | order by Timestamp desc | take 5

-- Link performance
LinkTelemetry | where LinkId == "LINK-SYD-MEL-FIBRE-01" | take 10
```

Table names depend on your Eventhouse database schema.

---

## Security

- **Read-only**: All queries pass through a guardrail that blocks write operations (`CREATE`, `DELETE`, `INSERT`, `.drop`, `.alter`, etc.) before they reach Fabric
- **Auto-LIMIT**: Queries without a LIMIT/take clause get one injected automatically (500 for GQL, 1000 for KQL) to prevent unbounded result sets
- **Token caching**: Fabric tokens are cached for 45 minutes with double-checked locking
- **Circuit breaker**: 3 consecutive failures → circuit opens → requests fast-fail for 60s cooldown
- **Concurrency**: Semaphore limits to 2 concurrent Fabric API calls (configurable via `FABRIC_MAX_CONCURRENT`)

---

## Architecture

```
User query → Tool call (query_graph / query_telemetry / query_alerts)
  → Read-only guardrail (reject mutations before execution)
  → Auto-inject LIMIT/take if missing
  → Acquire semaphore slot (concurrency=2)
  → Check circuit breaker (3 failures → open)
  → Execute query:
      GQL: HTTP POST to Fabric Graph Model API (Bearer token)
      KQL: Azure Kusto SDK (credential-based)
  → Retry on 429 / ColdStartTimeout / continuation
  → Release semaphore, record success/failure
  → Return JSON results
```

---

## File Structure

```
fabric_tools/
├── .env.example          # Template — copy to .env and fill in
├── .env                  # Your credentials (git-ignored)
├── pyproject.toml        # Dependencies (uv sync)
├── test_tools.py         # Run this first — tests all tools
├── test_with_agent.py    # Optional — test with AI agent
├── README.md             # This file
└── fabric_tools/         # The package (import from here)
    ├── __init__.py        # Exports: query_graph, query_telemetry, query_alerts, get_ontology
    ├── _auth.py           # Token management (45-min cache)
    ├── _constants.py      # Env var configuration
    ├── _credentials.py    # Azure credential factory (SP → MI → CLI)
    ├── _guardrails.py     # Read-only + auto-LIMIT
    ├── _resilience.py     # Circuit breaker
    ├── _stubs.py          # Simplified config (replaces PathfinderIQ internals)
    ├── _throttle.py       # Semaphore + circuit breaker
    ├── graph/
    │   ├── _query.py      # query_graph() — GQL via Fabric REST API
    │   └── _ontology.py   # get_ontology() — schema discovery
    └── telemetry/
        ├── _query.py      # query_telemetry() — KQL via Kusto SDK
        └── _alerts.py     # query_alerts() — KQL for alerts
```
