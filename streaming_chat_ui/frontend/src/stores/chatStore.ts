/**
 * Chat store — message list, streaming state, and SSE integration.
 *
 * Zustand store that owns all chat-related state: messages, streaming
 * status, content parts accumulation, abort controller, and timing metrics.
 *
 * State machine:
 *   IDLE → user sends message → STREAMING → events arrive → DONE/ERROR
 *                                    ↓
 *                                  ABORT → ABORTED
 */

import { create } from "zustand";
import type {
  Message,
  ToolCall,
  StreamMetadata,
  ContentPart,
} from "@/api/types";
import * as api from "@/api/client";
import { useSessionStore } from "./sessionStore";
import { generateToolSummary, legacyToParts } from "@/features/chat/partUtils";
import { buildAssistantMessage } from "@/features/chat/messageBuilder";
import { syncMessageIds } from "@/features/chat/idSync";

type ChatStatus = "idle" | "streaming" | "error";

// ── Store interface ─────────────────────────────────────────────────────────

interface ChatState {
  messages: Message[];
  status: ChatStatus;
  streamingParts: ContentPart[];
  streamingToolIndex: Map<string, number>;
  _toolStartTimes: Map<string, number>;
  lastMetadata: StreamMetadata | null;
  error: string | null;
  rateLimitCountdown: number | null;
  _sendTimestamp: number | null;
  _firstTokenReceived: boolean;
  ttftMs: number | null;
  ttltMs: number | null;
  _abortController: AbortController | null;
  _streamingTimeout: ReturnType<typeof setTimeout> | null;

  sendMessage: (sessionId: string, content: string) => Promise<void>;
  abort: (sessionId: string) => void;
  setMessages: (messages: Message[]) => void;
  loadSessionMessages: (messages: Message[]) => void;
  clearChat: () => void;
}

// ── Store ────────────────────────────────────────────────────────────────────

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  status: "idle",
  streamingParts: [],
  streamingToolIndex: new Map(),
  _toolStartTimes: new Map(),
  lastMetadata: null,
  error: null,
  rateLimitCountdown: null,
  _sendTimestamp: null,
  _firstTokenReceived: false,
  ttftMs: null,
  ttltMs: null,
  _abortController: null,
  _streamingTimeout: null,

  sendMessage: async (sessionId: string, content: string) => {
    const state = get();
    if (state.status === "streaming") return;

    // Create temp user message
    const userMessage: Message = {
      id: `temp-${Date.now()}`,
      role: "user",
      content,
      parts: [{ type: "text", text: content }],
      status: "complete",
      tool_calls: [],
      created_at: new Date().toISOString(),
    };

    const abortController = new AbortController();

    set({
      messages: [...state.messages, userMessage],
      status: "streaming",
      streamingParts: [],
      streamingToolIndex: new Map(),
      error: null,
      rateLimitCountdown: null,
      _abortController: abortController,
      _streamingTimeout: null,
      _sendTimestamp: Date.now(),
      _firstTokenReceived: false,
      ttftMs: null,
      ttltMs: null,
    });

    // Idle-timeout guard (300s)
    const IDLE_TIMEOUT_MS = 300_000;

    const _fireStreamTimeout = () => {
      const cur = get();
      if (cur.status !== "streaming") return;
      if (cur.streamingParts.length > 0) {
        const partialMsg = buildAssistantMessage(cur.streamingParts, "error");
        set({
          status: "error",
          error: "Stream timed out — partial response preserved above.",
          messages: [...cur.messages, partialMsg],
          streamingParts: [],
          _abortController: null,
          _streamingTimeout: null,
        });
      } else {
        set({
          status: "error",
          error: "Stream timed out — no response received.",
          _abortController: null,
          _streamingTimeout: null,
        });
      }
    };

    const _resetIdleTimeout = (ms: number = IDLE_TIMEOUT_MS) => {
      const cur = get();
      if (cur._streamingTimeout) clearTimeout(cur._streamingTimeout);
      const tid = setTimeout(_fireStreamTimeout, ms);
      set({ _streamingTimeout: tid });
    };

    _resetIdleTimeout();

    try {
      await api.streamChat(
        sessionId,
        { content },
        {
          onToken: (token) => {
            _resetIdleTimeout();
            const st = get();
            // Track TTFT
            if (!st._firstTokenReceived && st._sendTimestamp) {
              const ttft = Date.now() - st._sendTimestamp;
              set({ _firstTokenReceived: true, ttftMs: ttft });
            }
            // Clear rate limit indicator
            if (get().rateLimitCountdown !== null) {
              set({ rateLimitCountdown: null });
            }
            // Append token to streaming parts
            set((s) => {
              const parts = [...s.streamingParts];
              const last = parts[parts.length - 1];
              if (last && (last.type === "text" || last.type === "thinking")) {
                parts[parts.length - 1] = { ...last, text: last.text + token };
              } else {
                parts.push({ type: "text", text: token });
              }
              return { streamingParts: parts };
            });
          },

          onToolCallStart: (id, name) => {
            _resetIdleTimeout();
            set((s) => {
              const parts = [...s.streamingParts];
              const now = Date.now();
              const toolCall: ToolCall = {
                id,
                name,
                arguments: {},
                status: "running",
                start_ms: now,
              };
              const newIndex = parts.length;
              parts.push({ type: "tool_call", toolCall });
              const toolIndex = new Map(s.streamingToolIndex);
              toolIndex.set(id, newIndex);
              const startTimes = new Map(s._toolStartTimes);
              startTimes.set(id, now);
              return {
                streamingParts: parts,
                streamingToolIndex: toolIndex,
                _toolStartTimes: startTimes,
              };
            });
          },

          onToolCallDelta: () => {},

          onToolCallEnd: (id, _name, args) => {
            _resetIdleTimeout();
            set((s) => {
              const idx = s.streamingToolIndex.get(id);
              if (idx === undefined) return s;
              const parts = [...s.streamingParts];
              const part = parts[idx];
              if (!part || part.type !== "tool_call") return s;
              parts[idx] = {
                type: "tool_call",
                toolCall: { ...part.toolCall, arguments: args },
              };
              return { streamingParts: parts };
            });
          },

          onToolResult: (id, name, result) => {
            _resetIdleTimeout();
            set((s) => {
              const idx = s.streamingToolIndex.get(id);
              if (idx === undefined) return s;
              const parts = [...s.streamingParts];
              const part = parts[idx];
              if (!part || part.type !== "tool_call") return s;
              const isError =
                result.includes('"error"') && result.includes("true");
              const startTime = s._toolStartTimes.get(id);
              const duration_ms = startTime ? Date.now() - startTime : null;
              parts[idx] = {
                type: "tool_call",
                toolCall: {
                  ...part.toolCall,
                  result,
                  status: isError ? "error" : "complete",
                  summary: generateToolSummary(name, result),
                  duration_ms,
                },
              };
              return { streamingParts: parts };
            });
          },

          onThinking: (title, detail) => {
            set((s) => ({
              streamingParts: [
                ...s.streamingParts,
                {
                  type: "thinking" as const,
                  text: `**${title}**: ${detail}`,
                },
              ],
            }));
          },

          onMetadata: (data) => {
            set({ lastMetadata: data as unknown as StreamMetadata });
          },

          onRateLimited: (retryAfter) => {
            set({ rateLimitCountdown: retryAfter });
            _resetIdleTimeout((retryAfter + 120) * 1000);
          },

          onKeepalive: () => {
            _resetIdleTimeout();
          },

          onError: (error) => {
            const cur = get();
            if (cur._streamingTimeout) clearTimeout(cur._streamingTimeout);
            if (cur.streamingParts.length > 0) {
              const partialMsg = buildAssistantMessage(
                cur.streamingParts,
                "error",
              );
              set({
                status: "error",
                error,
                messages: [...cur.messages, partialMsg],
                streamingParts: [],
                rateLimitCountdown: null,
                _abortController: null,
                _streamingTimeout: null,
              });
            } else {
              set({
                status: "error",
                error,
                rateLimitCountdown: null,
                _abortController: null,
                _streamingTimeout: null,
              });
            }
          },

          onDone: () => {
            const cur = get();
            if (cur._streamingTimeout) clearTimeout(cur._streamingTimeout);
            const ttlt = cur._sendTimestamp
              ? Date.now() - cur._sendTimestamp
              : null;
            if (ttlt) set({ ttltMs: ttlt });

            const assistantMessage = buildAssistantMessage(
              cur.streamingParts,
              "complete",
            );

            set({
              status: "idle",
              messages: [...cur.messages, assistantMessage],
              streamingParts: [],
              streamingToolIndex: new Map(),
              rateLimitCountdown: null,
              _abortController: null,
              _streamingTimeout: null,
            });

            // Sync IDs with server
            useSessionStore
              .getState()
              .refreshActiveSession()
              .then(() => {
                const session = useSessionStore.getState().activeSession;
                if (!session) return;
                const localMessages = get().messages;
                const serverMessages = (session.messages ?? []).filter(
                  (m: Message) => m.role !== "system",
                );
                set({
                  messages: syncMessageIds(localMessages, serverMessages),
                });
              });
          },

          onAborted: () => {
            const cur = get();
            const abortedMessage = buildAssistantMessage(
              cur.streamingParts,
              "aborted",
            );

            set({
              status: "idle",
              messages: [...cur.messages, abortedMessage],
              streamingParts: [],
              streamingToolIndex: new Map(),
              _abortController: null,
            });

            useSessionStore
              .getState()
              .refreshActiveSession()
              .then(() => {
                const session = useSessionStore.getState().activeSession;
                if (!session) return;
                const localMessages = get().messages;
                const serverMessages = (session.messages ?? []).filter(
                  (m: Message) => m.role !== "system",
                );
                set({
                  messages: syncMessageIds(localMessages, serverMessages),
                });
              });
          },
        },
        abortController.signal,
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        set({
          status: "error",
          error: (err as Error).message,
          _abortController: null,
        });
      }
    }
  },

  abort: (sessionId: string) => {
    const state = get();
    if (state._abortController) {
      state._abortController.abort();
      set({ _abortController: null });
    }
    api.abortGeneration(sessionId).catch(() => {});
  },

  setMessages: (messages: Message[]) => {
    set({
      messages: messages.map((m) => ({
        ...m,
        parts: m.parts?.length ? m.parts : legacyToParts(m),
      })),
    });
  },

  loadSessionMessages: (messages: Message[]) => {
    const displayMessages = messages.filter(
      (m: Message) => m.role !== "system",
    );

    // Restore lastMetadata from the last assistant message's context_snapshot
    let restoredMetadata: StreamMetadata | null = null;
    for (let i = displayMessages.length - 1; i >= 0; i--) {
      const msg = displayMessages[i];
      if (msg.role === "assistant" && msg.context_snapshot) {
        const cs = msg.context_snapshot as Record<string, unknown>;
        restoredMetadata = {
          prompt_tokens:
            (cs.prompt_tokens as number) || (cs.tokens_used as number) || 0,
          completion_tokens: (cs.completion_tokens as number) || 0,
          total_tokens:
            (cs.total_tokens as number) || (cs.tokens_used as number) || 0,
          duration_ms: (cs.duration_ms as number) || 0,
          model: (cs.model as string) || "",
          assistant_message_id: msg.id,
          estimated_cost_usd: (cs.estimated_cost_usd as number) || undefined,
        };
        break;
      }
    }

    set({
      messages: displayMessages.map((m: Message) => ({
        ...m,
        parts: m.parts?.length ? m.parts : legacyToParts(m),
      })),
      ...(restoredMetadata ? { lastMetadata: restoredMetadata } : {}),
    });
  },

  clearChat: () => {
    const state = get();
    if (state._abortController) state._abortController.abort();
    if (state._streamingTimeout) clearTimeout(state._streamingTimeout);
    set({
      messages: [],
      status: "idle",
      streamingParts: [],
      streamingToolIndex: new Map(),
      _toolStartTimes: new Map(),
      lastMetadata: null,
      error: null,
      rateLimitCountdown: null,
      _sendTimestamp: null,
      _firstTokenReceived: false,
      ttftMs: null,
      ttltMs: null,
      _abortController: null,
      _streamingTimeout: null,
    });
  },
}));
