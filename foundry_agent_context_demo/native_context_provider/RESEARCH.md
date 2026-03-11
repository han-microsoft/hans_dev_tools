# Native Context Provider Pattern — Research Findings

## SDK Source: agent_framework 1.0.0rc3

The agent_framework SDK has a built-in **context engineering pipeline** via
`BaseContextProvider` and `BaseHistoryProvider`. This lets you control what
conversation history the agent sees WITHOUT the PathfinderIQ-style message
injection hack.

---

## How It Works

### The Provider Lifecycle

```
agent.run(message, session=session)
  │
  ├── 1. Auto-inject InMemoryHistoryProvider (if no providers configured)
  │
  ├── 2. for provider in self.context_providers:
  │       provider.before_run(agent, session, context, state)
  │         └── Loads history → context.extend_messages(self, messages)
  │
  ├── 3. Merge context_messages + input_messages → LLM prompt
  │
  ├── 4. LLM invocation (streaming)
  │
  └── 5. for provider in reversed(self.context_providers):
            provider.after_run(agent, session, context, state)
              └── Saves input + response messages
```

### Key Classes (from _sessions.py)

**BaseContextProvider** — Hook interface:
- `source_id: str` — unique ID for this provider
- `before_run(agent, session, context, state)` — add context before invocation
- `after_run(agent, session, context, state)` — process response after invocation

**BaseHistoryProvider(BaseContextProvider)** — Conversation memory:
- `load_messages: bool` — whether to load history before invocation
- `store_inputs: bool` — whether to save user messages
- `store_outputs: bool` — whether to save assistant responses
- `get_messages(session_id, state)` → list of stored messages
- `save_messages(session_id, messages, state)` → persist messages
- Default `before_run` calls `get_messages()` and adds to context
- Default `after_run` calls `save_messages()` with input + response

**InMemoryHistoryProvider(BaseHistoryProvider)** — Built-in default:
- Stores messages in `session.state["messages"]`
- Auto-injected when no providers configured and session is provided
- All data lives in the session's state dict (no instance state)

**SessionContext** — The context being built:
- `context_messages: dict[str, list[Message]]` — keyed by source_id
- `input_messages: list[Message]` — the current user message
- `extend_messages(source, messages)` — add history from a provider
- `extend_instructions(source_id, text)` — add dynamic instructions
- `extend_tools(source_id, tools)` — add runtime tools

**AgentSession** — Lightweight state container:
- `session_id: str` — unique session ID
- `state: dict[str, Any]` — mutable shared state (providers store data here)
- Serializable via `to_dict()` / `from_dict()` for persistence

### The Auto-Inject Behavior

When you call `agent.run(msg, session=session)`:
1. If no `context_providers` registered AND no service-side storage → auto-adds `InMemoryHistoryProvider()`
2. This means ALL messages accumulate in `session.state["messages"]`
3. This is the problem the colleague has — no way to trim

### The Solution: Custom HistoryProvider with Window

Subclass `BaseHistoryProvider` and override `get_messages()` to return
only the last N messages:

```python
class WindowedHistoryProvider(BaseHistoryProvider):
    def __init__(self, max_messages: int = 10):
        super().__init__(
            source_id="windowed_history",
            load_messages=True,
            store_inputs=True,
            store_outputs=True,
        )
        self.max_messages = max_messages

    async def get_messages(self, session_id, *, state=None, **kwargs):
        if state is None:
            return []
        all_msgs = state.get("messages", [])
        # Return only the last N messages
        return all_msgs[-self.max_messages:]

    async def save_messages(self, session_id, messages, *, state=None, **kwargs):
        if state is None:
            return
        existing = state.get("messages", [])
        state["messages"] = [*existing, *messages]
```

Then register it on the agent:
```python
agent = client.as_agent(
    name="MyAgent",
    instructions="...",
    context_providers=[WindowedHistoryProvider(max_messages=10)],
)
```

### Key Advantages Over PathfinderIQ Pattern

1. **No message injection hack** — the SDK handles context assembly natively
2. **Proper message typing** — messages are `Message` objects with role, content, tool_calls
3. **Session persistence** — `AgentSession.to_dict()/from_dict()` for serialization
4. **Composable** — stack multiple providers (e.g., windowed history + RAG context)
5. **Source attribution** — each provider's messages are tagged with source_id
6. **Bidirectional** — `after_run` saves both input AND output automatically

### Important: Reuse the Session

Unlike PathfinderIQ's "fresh session per request" pattern:
- **KEEP the same AgentSession** across requests — it accumulates state
- The `WindowedHistoryProvider.get_messages()` slices the window each time
- The full history is in `session.state["messages"]` — the provider just reads a subset
- This is cleaner: no <prior_conversation> injection, proper Message objects

### What the SDK Does Under the Hood

From `_agents.py` line 1018-1028:
```python
# Auto-inject InMemoryHistoryProvider when session is provided,
# no context providers registered, and no service-side storage
if (
    session is not None
    and not self.context_providers
    and not session.service_session_id
    ...
):
    self.context_providers.append(InMemoryHistoryProvider())
```

By registering our own `WindowedHistoryProvider`, we prevent the auto-injection
of `InMemoryHistoryProvider` — the SDK uses OUR provider instead.
