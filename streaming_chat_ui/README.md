# Streaming Chat UI

A standalone, reusable streaming chat component with full SSE support, tool call rendering, and abort capabilities.

Extracted from the `graph_workshop` codebase — all graph/scenario/multi-agent/auth concerns removed.

## Architecture

```
streaming_chat_ui/
├── .env.example         ← Copy to .env, fill in your values
├── .env                 ← Default: echo provider (zero-config dev)
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py      ← FastAPI app (lifespan, CORS, router registration)
│       ├── config.py    ← Settings from .env via pydantic-settings
│       ├── models.py    ← Pydantic models (Session, Message, StreamEvent, etc.)
│       ├── routers/
│       │   ├── chat.py      ← POST /api/chat/{session_id} — SSE streaming
│       │   └── sessions.py  ← Session CRUD endpoints
│       └── services/
│           ├── context.py       ← Token counting + sliding-window trimming
│           ├── llm.py           ← LLMService protocol + factory + echo/mock providers
│           ├── llm_openai.py    ← OpenAI / Azure OpenAI provider
│           ├── llm_agent.py     ← Azure AI Foundry Agent provider
│           └── session_store.py ← In-memory session store
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tailwind.config.js
    └── src/
        ├── App.tsx              ← Root: sidebar + chat panel
        ├── main.tsx             ← React entry point
        ├── index.css            ← Tailwind + CSS variables (dark theme)
        ├── api/
        │   ├── types.ts         ← TS types mirroring backend models
        │   ├── client.ts        ← HTTP client + session CRUD
        │   └── chatApi.ts       ← SSE streaming (fetch + ReadableStream)
        ├── stores/
        │   ├── chatStore.ts     ← Messages, streaming state, SSE callbacks
        │   └── sessionStore.ts  ← Session list + active session
        ├── features/chat/
        │   ├── messageBuilder.ts ← Build Message from streaming parts
        │   ├── partUtils.ts      ← ContentPart helpers + tool summaries
        │   └── idSync.ts         ← Temp ID → server ID reconciliation
        ├── hooks/
        │   └── useAutoScroll.ts  ← Smart auto-scroll during streaming
        └── components/
            ├── shared/
            │   └── MarkdownRenderer.tsx  ← React-markdown + syntax highlighting
            └── chat/
                ├── ChatPanel.tsx         ← Composes MessageList + ChatInput
                ├── MessageList.tsx       ← Scrollable message timeline
                ├── MessageBubble.tsx     ← User/assistant message rendering
                ├── ChatInput.tsx         ← Textarea + send/abort
                ├── StreamingIndicator.tsx ← Animated typing dots
                ├── ToolCallDisplay.tsx   ← Collapsible tool call card
                ├── TextBlock.tsx         ← Debounced markdown text
                └── ThinkingBlock.tsx     ← Agent reasoning display
```

## Quick Start (Zero Config)

### 1. Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The default `.env` uses `LLM_PROVIDER=echo` — the backend echoes your message back word-by-word. No API keys needed.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 — the Vite dev server proxies `/api` to the backend.

## LLM Providers

Set `LLM_PROVIDER` in `.env`:

| Provider | Value | Requirements |
|----------|-------|-------------|
| **Echo** | `echo` | None — parrots user message back |
| **Mock** | `mock` | None — canned response with tool calls, markdown, tables |
| **OpenAI** | `openai` | `LLM_API_KEY` + optionally `LLM_BASE_URL` |
| **Azure OpenAI** | `openai` | `LLM_API_KEY` + `LLM_BASE_URL` (Azure endpoint) |
| **Azure AI Foundry** | `agent` | `AZURE_AI_PROJECT_ENDPOINT` + `AZURE_AI_AGENT_ID` |

### Azure AI Foundry Setup

```bash
# .env
LLM_PROVIDER=agent
AZURE_AI_PROJECT_ENDPOINT=https://<hub>.services.ai.azure.com/api/projects/<project>
AZURE_AI_AGENT_ID=<your-agent-id>
```

Install the extra dependencies:

```bash
pip install azure-ai-projects azure-identity
```

Uses `DefaultAzureCredential` — authenticate via `az login` locally.

## SSE Protocol

The chat endpoint (`POST /api/chat/{session_id}`) streams typed SSE events:

| Event | Data | Purpose |
|-------|------|---------|
| `token` | `{token: string}` | Text content delta |
| `tool_call_start` | `{id, name}` | Tool invocation begins |
| `tool_call_end` | `{id, name, arguments}` | Tool arguments complete |
| `tool_result` | `{id, name, result}` | Tool execution result |
| `thinking` | `{title, detail}` | Agent reasoning step |
| `metadata` | `StreamMetadata` | Token usage, timing, cost |
| `keepalive` | `{}` | 15s heartbeat (prevents timeout) |
| `error` | `{error, error_code?}` | Error occurred |
| `done` | `{}` | Stream complete |
| `aborted` | `{}` | User cancelled |

### Abort Support

```
POST /api/chat/{session_id}/abort
```

Sets an abort event monitored by the keepalive wrapper. The SSE stream emits an `aborted` event and terminates.

## Key Features

- **Interleaved content parts**: Messages render thinking → tool calls → text in chronological order
- **Live tool call timers**: `start_ms` on tool calls enables accurate elapsed time across tab switches
- **Debounced markdown rendering**: 150ms throttle during streaming prevents CPU-heavy re-parses
- **Smart auto-scroll**: Follows new content but pauses when user scrolls up
- **Keepalive heartbeat**: 15s interval prevents proxy/browser timeout during long operations
- **Idle timeout**: 300s client-side guard against hung streams
- **ID reconciliation**: Temp local IDs synced with server-assigned IDs after stream completes
- **Context windowing**: Token-budgeted sliding window with newest-first filling

## Customization

### Theming

Edit CSS variables in `frontend/src/index.css`:

```css
:root {
  --color-brand: #6366f1;       /* Primary accent */
  --color-bg-1: #0f1117;        /* Darkest background */
  --color-bg-2: #1a1d27;        /* Card/bubble background */
  --color-text-primary: #e4e5eb; /* Main text */
  /* ...etc */
}
```

### Adding a New LLM Provider

1. Create `backend/app/services/llm_<name>.py` implementing `stream_completion(messages, abort_event) → AsyncIterator[StreamEvent]`
2. Add a case to the factory in `backend/app/services/llm.py`:
   ```python
   elif provider == "<name>":
       from app.services.llm_<name> import MyService
       return MyService()
   ```
3. Set `LLM_PROVIDER=<name>` in `.env`

### Adding Tool Call Icons

Edit `TOOL_ICONS` in `frontend/src/components/chat/ToolCallDisplay.tsx`:

```typescript
const TOOL_ICONS: Record<string, string> = {
  my_custom_tool: "🛠️",
  // ...
};
```
