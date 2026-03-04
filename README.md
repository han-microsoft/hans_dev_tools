# Han's Toolkit

Reusable components extracted from the Graph Workshop codebase. Each component is self-contained — copy it into your project and wire it up.

## Components

### 1. `streaming_chat_ui/` — Streaming Chat Interface

Full-stack streaming chat with SSE, tool call rendering, and abort support.

**Backend** (Python): FastAPI + SSE with keepalive heartbeat, token-budgeted context window, pluggable LLM providers (OpenAI, Azure OpenAI, Azure AI Foundry Agent, echo/mock for dev).

**Frontend** (TypeScript): React + Zustand + Tailwind with manual SSE frame parsing, interleaved content parts (thinking → tool calls → text), debounced markdown rendering, live tool call timers, smart auto-scroll, and idle timeout guards.

```bash
# Zero-config dev startup (echo provider, no API keys needed)
cd streaming_chat_ui/backend && pip install -r requirements.txt && uvicorn app.main:app --port 8000
cd streaming_chat_ui/frontend && npm install && npm run dev
# Open http://localhost:5173
```

Set `LLM_PROVIDER` in `.env` to switch providers: `echo` | `mock` | `openai` | `agent`.

[Full docs →](streaming_chat_ui/README.md)

---

### 2. `graph_viz/` — Interactive Graph Visualization

Two pluggable graph visualization backends sharing types, hooks, and constants:

- **Force Graph** (`react-force-graph-2d`) — Canvas-rendered force-directed layout with physics simulation, search, colour editor, label controls, context menu, and tooltips.
- **React Flow** (`@xyflow/react` + `dagre`) — DOM-rendered hierarchical layout with card nodes, animated edges, MiniMap, drag repositioning, and filtering.

Both consume the same `topology.json` format (`nodes[]` + `edges[]`). Use one or both — zero cross-backend imports.

```bash
cd graph_viz && npm install && npm run dev
# Drop your topology.json in public/ and open http://localhost:5173
```

[Full docs →](graph_viz/README.md)

---

### 3. `fabric_data_loader/` — Microsoft Fabric Scenario Deployer

Single-command deployment of graph data scenarios to Microsoft Fabric:

```
auth → workspace → folder → lakehouse → eventhouse → ontology → verify
```

Creates Fabric workspace resources, uploads entity CSVs to Lakehouse (delta tables via OneLake), ingests telemetry into Eventhouse (KQL tables), generates and deploys a Graph ontology from `graph_schema.yaml`, and writes all discovered resource IDs back to an env file.

Includes a **sample bookstore scenario** with dummy data (5 authors, 10 books, 5 publishers, edges, and telemetry events) ready to deploy.

```bash
cd fabric_data_loader/scripts
az login
python3 deploy_scenario.py --manifest ../sample_scenario/deploy_manifest.yaml
```

[Full docs →](fabric_data_loader/README.md)

---

### 4. `ai_search_indexer/` — Azure AI Search Index Deployer

Manifest-driven deployment of Azure AI Search indexes with vector search and semantic ranking. One command to:

1. Upload knowledge docs (markdown, text) to Azure Blob Storage
2. Create search indexes with HNSW vector search + Azure OpenAI vectorizer
3. Build chunking + embedding skillsets (SplitSkill → AzureOpenAIEmbeddingSkill)
4. Run indexers and poll until complete

Pre-flight checks auto-fix common issues (storage public access, Search RBAC, missing containers).

Includes a **sample ops scenario** with dummy runbooks, incident tickets, and equipment manifests.

```bash
cd ai_search_indexer/scripts
cp ../.env.example ../azure_config.env   # Edit with your Azure resource names
az login
python3 deploy_scenario.py --manifest ../sample_scenario/search_manifest.yaml --upload-files
# Or dry-run (no Azure credentials needed):
python3 deploy_scenario.py --manifest ../sample_scenario/search_manifest.yaml --dry-run
```

[Full docs →](ai_search_indexer/README.md)

---

## How to Vibecode Your Own App

### Chat app with tool use

1. Copy `streaming_chat_ui/` into your project
2. Set `LLM_PROVIDER=agent` and your Foundry endpoint in `.env`
3. The backend already handles the full SSE protocol — add your own tools in the Foundry agent
4. The frontend renders tool calls, thinking steps, and markdown out of the box
5. Customize tool icons in `ToolCallDisplay.tsx`, theme colours in `index.css`

### Chat app with graph visualization

1. Copy both `streaming_chat_ui/` and `graph_viz/`
2. Mount the graph component alongside the chat panel in your layout
3. Have your agent's tools return graph topology data
4. Feed topology updates into the graph viewer via `useTopology` hook

### Graph-powered app on Fabric

1. Define your domain as CSVs (entities + telemetry) and a `graph_schema.yaml`
2. Use `fabric_data_loader/` to deploy everything to Fabric in one command
3. Wire the chat backend to query your Fabric Lakehouse (GQL) and Eventhouse (KQL)
4. The ontology maps directly to what your agent can query

### Just the streaming protocol

If you only need the SSE streaming layer:
- **Backend**: Copy `routers/chat.py` + `services/llm.py` — the keepalive wrapper, abort events, and SSE formatter are all in one file
- **Frontend**: Copy `api/chatApi.ts` + `stores/chatStore.ts` + `features/chat/` — the streaming state machine, message builder, and ID sync are self-contained

### Just the graph visualization

Copy the backend you want from `graph_viz/src/`:
- Force Graph: `ForceGraph/` folder + `hooks/useTopology.ts` + `constants.ts`
- React Flow: `ReactFlow/` folder + `hooks/useTopology.ts` + `constants.ts`

Replace the fetch URL in `useTopology.ts` to point at your API.

### RAG-powered chat with AI Search

1. Use `ai_search_indexer/` to index your knowledge docs (runbooks, policies, specs — anything markdown or text)
2. Copy `streaming_chat_ui/` for the chat interface
3. Wire your agent's tools to query the search indexes (hybrid search: keyword + vector + semantic reranking)
4. The search indexes chunk, embed, and serve your docs — no custom embedding pipeline needed
