# Context Management for Azure AI Foundry Agents
## Comparative Analysis: PathfinderIQ Injection vs Native Context Provider

**Date**: 2026-03-11
**Scope**: Two approaches to controlling conversation context in Foundry agents
**SDK Version**: agent-framework 1.0.0rc3, agent-framework-azure-ai 1.0.0rc3

---

## Executive Summary

The Azure AI Agent Framework SDK's default behavior loads ALL conversation
history into every LLM invocation. Two patterns exist to override this and
control exactly what context the agent sees. This report compares them across
five dimensions.

| Dimension | PathfinderIQ Injection | Native Context Provider |
|-----------|----------------------|------------------------|
| **Mechanism** | Fresh session + text injection | Persistent session + provider hook |
| **Context control** | String manipulation | SDK lifecycle hooks |
| **Session model** | Stateless (new per request) | Stateful (reused across requests) |
| **Multi-user** | Trivially safe | Requires session-per-user |
| **Complexity** | Lower | Higher |
| **SDK coupling** | Minimal | Deep |

---

## 1. How Each Approach Works

### 1.1 PathfinderIQ Style: Message Injection

```
Per request:
  1. Chat router persists user message to session store (Cosmos/in-memory)
  2. SessionStateManager.build_turn_context() builds token-budgeted context:
     a. Extract agent's thread messages (thread-isolated, no cross-agent bleed)
     b. Separate system prompt (message 0) from conversation messages
     c. Optional pre-slice by max_turns (coarse depth control)
     d. Fill newest-first within token budget (max_context_tokens - max_response_tokens)
     e. Result: list of OpenAI chat-completion format dicts
  3. _filter_prior_messages() extracts user/assistant/tool messages, drops current query
  4. _build_context_injection() formats as <prior_conversation> XML block
     - Includes content text (truncated to 2000 chars)
     - Includes tool call NAMES only (not arguments or results)
     - Prepended to the current user message string
  5. Agent is REBUILT per request via AgentRegistry.build() (with model fallback)
  6. Fresh AgentSession() created per attempt (also fresh on retry)
  7. agent.run(augmented_message, stream=True, session=fresh_session)
```

Key detail: PathfinderIQ does NOT use simple "last N messages" — it uses a
**token-budgeted sliding window** (newest-first priority filling). The context
window can hold 50+ messages if they're short, or 3 messages if they contain
large tool results. The `max_turns` parameter provides an optional coarse cap
on top of the token budget. The demo simplifies this to "last N messages" for
clarity, but production PathfinderIQ is significantly more sophisticated.

The agent receives a single string that embeds the context:
```
<prior_conversation>
The following is the conversation history from this session.
Use it as context for the current question.

USER: My name is Alice
ASSISTANT [tools used: query_graph]: The network shows...
USER: What alerts are active?
ASSISTANT [tools used: query_alerts, search_runbooks]: Found 3 active alerts...
</prior_conversation>

What is my name?
```

The instructions tell the agent to consume this block silently. The SDK creates
a new server-side thread for every request (and every retry attempt) and
discards it after.

**Agent rebuild per request**: Unlike the demo (which caches the agent), the
production PathfinderIQ rebuilds the agent via `AgentRegistry.build()` on
every request. This is necessary because the model fallback queue may switch
models between attempts, and per-agent client isolation prevents SDK state
bleed across different agent identities.

### 1.2 Native Style: WindowedHistoryProvider

```
Per request:
  1. Pass raw user message (no modification)
  2. Reuse the SAME AgentSession across requests
  3. SDK calls provider.before_run() → loads last N messages from session.state
  4. SDK assembles context: loaded history + current message → LLM prompt
  5. agent.run(user_message, stream=True, session=persistent_session)
  6. SDK calls provider.after_run() → saves input + response to session.state
```

The `WindowedHistoryProvider` overrides `get_messages()` to return only the
last N items from the accumulated history:

```python
async def get_messages(self, session_id, *, state=None, **kwargs):
    all_msgs = state.get("messages", [])
    return all_msgs[-self.max_messages:]  # Window slides automatically
```

---

## 2. Performance Implications

### 2.1 Token Consumption

| Factor | PathfinderIQ | Native |
|--------|-------------|--------|
| Context format | Plain text in user message | Typed Message objects |
| Overhead per turn | `<prior_conversation>` wrapper (~80 tokens) | Zero wrapper overhead |
| Tool call representation | Names only (`[tools used: X, Y]`) — no args/results | Full structured Message with tool_calls, arguments, results |
| Content truncation | Truncated to 2000 chars per message | Full content preserved |
| Context sizing | Token-budgeted sliding window (production) | Provider returns last N Message objects |
| Max context consumed | System prompt + token-budgeted history + injected wrapper | History messages + user message (no wrapper) |

**PathfinderIQ uses more tokens** per turn due to: (a) the XML wrapper
(`<prior_conversation>`, `</prior_conversation>`, header text — ~80 tokens),
(b) role labels on every message (`USER:`, `ASSISTANT [tools used: ...]:` —
~10 tokens per message), and (c) the entire injected block counts as the user
message, not as separate chat-completion messages with proper role separation.

The bigger difference: **tool call representation**. PathfinderIQ's injection
includes only tool NAMES (`[tools used: query_graph]`) — it discards arguments
and results entirely. The content field carries the assistant's text response
(truncated to 2000 chars), but the structured tool call graph is lost. The
native provider stores full `Message` objects with typed `tool_calls` lists
including arguments and results. This gives the native approach significantly
better tool re-invocation accuracy on longer conversations with complex tool
chains.

### 2.2 Server-Side Thread Overhead

**PathfinderIQ**: Creates a new Foundry thread per request AND per retry
attempt. Each `AgentSession()` triggers a `POST /threads` call server-side.
The agent itself is also rebuilt per request via `AgentRegistry.build()` to
support model fallback (trying gpt-5.2 → gpt-4.1 on rate limit). For a
10-turn conversation with no retries, that's 10 thread creations + 10 agent
builds.

**Native**: Creates ONE thread on the first request. Subsequent requests reuse
the same session. For 10 turns: 1 thread creation + 9 reuses. ~9 fewer HTTP
round-trips for thread setup.

**Estimated per-turn savings**: 100–300ms of thread creation overhead on the
native approach after the first turn.

### 2.3 Streaming Latency

Both approaches use the same streaming path: `agent.run(stream=True)` →
`AgentResponseUpdate` → yield text chunks. **Time to first token (TTFT) is
identical** for the same message length. The only difference is input size:
PathfinderIQ's injected message is slightly larger due to the wrapper text.

---

## 3. Session and User Isolation

This is the most critical difference for production systems.

### 3.1 PathfinderIQ: Trivially Isolated

```
User A sends message → fresh AgentSession() → context from User A's history
User B sends message → fresh AgentSession() → context from User B's history
```

Sessions are **stateless by design**. There is ZERO shared state between
requests. User isolation is guaranteed by construction — each request
builds its own context from the caller-provided history list. Even if the
same agent object handles both users, no state leaks.

**Multi-tenant safety**: The caller (router/handler) owns the history. As long
as the router resolves the correct user's conversation, isolation is automatic.
PathfinderIQ's production implementation stores conversations per-user in
Cosmos DB and loads them per-request.

### 3.2 Native: Requires Explicit Session Management

```
User A sends message → session_A → provider loads from session_A.state
User B sends message → session_B → provider loads from session_B.state
```

Sessions are **stateful**. The `AgentSession` accumulates messages in its
`state` dict across requests. If two users share the same session object,
**their conversations bleed into each other**.

**Multi-tenant safety**: The application must:
1. Create and store a separate `AgentSession` per user (or per conversation)
2. Look up the correct session on each request
3. Ensure sessions are not shared across users
4. Serialize/deserialize sessions for persistence (`session.to_dict()`/`from_dict()`)

This is more work but provides a richer abstraction — the session is a
first-class persistable object with built-in serialization.

### 3.3 Risk Assessment

| Risk | PathfinderIQ | Native |
|------|-------------|--------|
| Cross-user data leak | **Impossible** (stateless) | **Possible** if session reused |
| Session corruption on crash | No state to corrupt | In-memory state lost |
| Concurrent access race | None (no shared state) | Session.state dict is not thread-safe |

For **demo/single-user** scenarios, both are equivalent. For **production
multi-tenant** systems, PathfinderIQ's stateless model is safer by default.
The native approach requires more careful session lifecycle management but
rewards you with structured persistence.

---

## 4. Reliability

### 4.1 Failure Modes

**PathfinderIQ**:
- If context injection produces malformed text → agent may misinterpret, but
  the error is visible in the prompt and debuggable
- If the history list is corrupted → only that request affected, next request
  starts fresh
- No server-side state to become inconsistent

**Native**:
- If `save_messages()` fails → history gap, subsequent turns lose context
- If `get_messages()` returns wrong data → agent sees incorrect history
- Session state corruption affects ALL subsequent requests on that session
- The `after_run` hook runs even on errors — partial responses may be saved
  as complete messages

### 4.2 Resumability

**PathfinderIQ**: Fully resumable. History is external (Cosmos, database, in-memory
list). If the server restarts, reload history from the store and continue.
No session state to reconstruct.

**Native**: Resumable IF you serialize `AgentSession` state. The SDK provides
`session.to_dict()` and `AgentSession.from_dict()` for this purpose. But you
must explicitly save and restore session state on each request or at shutdown.
If you don't, a server restart loses all conversation history.

### 4.3 Context Accuracy

**PathfinderIQ**: Context accuracy depends on the quality of the text
serialization. The `<prior_conversation>` block is plain text — role labels
and truncation are correct, but tool call arguments are summarized, not
preserved verbatim. The agent's ability to reference specific prior tool
outputs degrades with summary quality.

**Native**: Context uses the SDK's typed `Message` objects with full `content`,
`tool_calls`, and `additional_properties`. The agent sees the same structured
data it would see with full history. Context accuracy is **structurally
identical to the no-windowing case** — the only difference is which messages
are included, not how they're represented.

---

## 5. Implementation Streamlining

### 5.1 Code Complexity

**PathfinderIQ** (from production codebase):
- `_filter_prior_messages()` — 15 lines (extract user/assistant/tool, drop current query)
- `_build_context_injection()` — 30 lines (format with role labels, tool name summaries, truncation)
- `build_context_window()` — 80 lines (token-budgeted sliding window, newest-first priority)
- `build_turn_context()` — 40 lines (thread isolation, system prompt extraction, snapshot)
- Fresh `AgentSession()` in the run loop — 1 line (also fresh on retry)
- Agent rebuild per request via `AgentRegistry.build()` — supports model fallback
- Instruction telling agent about `<prior_conversation>` — 2 lines
- **Total: ~170 lines across 4 modules (context, session_state, agent, instructions)**

**Native**:
- `WindowedHistoryProvider` class — 30 lines
- `context_providers=[...]` on agent build — 1 line
- Persistent session management — depends on architecture
- **Total: ~30 lines of provider code + session lifecycle**

The native approach has far fewer lines of custom code. However, PathfinderIQ's
additional complexity serves real purposes: token-budgeted windowing (not just
message count), per-request agent rebuild for model fallback, and per-thread
isolation for multi-agent delegation. A production native implementation would
likely grow toward similar complexity as these requirements emerge.

### 5.2 Testability

**PathfinderIQ**: `build_context_injection()` is a pure function — input
history + message → output string. Unit testing is trivial. No mocks needed.

**Native**: `WindowedHistoryProvider` requires constructing `AgentSession`,
`SessionContext`, and `state` dicts to test. The `before_run`/`after_run`
lifecycle requires simulating the SDK's invocation pipeline. Integration
testing is the practical approach.

### 5.3 Extensibility

**PathfinderIQ**: To change the context strategy (e.g., from windowed to
token-budgeted), modify `build_context_injection()`. One function, one
change point, no SDK interaction.

**Native**: To change the strategy, modify `get_messages()` in the provider.
Same single change point, but within the SDK's provider contract. You can also
**compose multiple providers** — e.g., a windowed history provider + a RAG
context provider + an evaluation logger, all running in the same pipeline.
This is the native approach's strongest advantage.

### 5.4 SDK Version Sensitivity

**PathfinderIQ**: Minimal SDK coupling. Uses only `agent.run(message)` and
`AgentSession()`. These are stable public APIs. A SDK version upgrade is
unlikely to break this pattern.

**Native**: Deep SDK coupling. Uses `BaseHistoryProvider` (currently a
"temporary name" per the source code — will be renamed to `HistoryProvider`
in a future release), `SessionContext.extend_messages()`, and the
`before_run`/`after_run` lifecycle. These are new APIs in rc3 and may change.
The import path `agent_framework._sessions` uses a private module prefix,
indicating the API is not yet stabilized.

---

## 6. Recommendations

### For the Colleague's Question

**"How do I pass only the last N messages to my Foundry agent?"**

Both approaches solve this. Recommend based on context:

- **Quick answer / demo**: PathfinderIQ injection. ~50 lines, zero SDK
  abstractions, works immediately, trivially safe for multi-user.

- **Production system with session persistence**: Native provider. Proper
  Message typing, composable with other providers, built-in serialization,
  but requires session-per-user management.

### For PathfinderIQ Itself

PathfinderIQ should **stay with the injection pattern** for now because:

1. It already works in production with multi-agent delegation, tool calls, and
   Cosmos-backed session persistence
2. The `BaseHistoryProvider` API uses a private module path and will be renamed
3. PathfinderIQ's multi-agent architecture requires per-agent thread isolation
   and per-request agent rebuilds for model fallback — the native provider
   doesn't address either of these concerns
4. The token-budgeted sliding window is strictly more capable than message-count
   windowing — porting it to a native provider would require reimplementing the
   token counting and priority-fill logic inside `get_messages()`
5. Migration risk exceeds benefit — the injection pattern is battle-tested
   across 800+ test cases

### When to Adopt Native Providers

Adopt when the SDK stabilizes the `BaseHistoryProvider` API (drops the `_`
prefix, finalizes the rename to `HistoryProvider`) AND your application needs:
- Composable context (multiple providers in a pipeline)
- Structured tool call history (not text summaries)
- Built-in session serialization (`to_dict`/`from_dict`)
- Evaluation/audit logging via store-only providers

---

## Appendix: Key SDK Source References

| File | Class/Function | Role |
|------|---------------|------|
| `_sessions.py:272` | `BaseContextProvider` | Hook interface (before_run/after_run) |
| `_sessions.py:339` | `BaseHistoryProvider` | History storage with load/store flags |
| `_sessions.py:522` | `InMemoryHistoryProvider` | Default auto-injected provider |
| `_sessions.py:452` | `AgentSession` | Lightweight state container with serialization |
| `_agents.py:1018` | Auto-inject logic | Adds InMemoryHistoryProvider when no providers configured |
| `_agents.py:1192` | before_run loop | Calls providers in forward order |
| `_agents.py:437` | after_run loop | Calls providers in reverse order |
