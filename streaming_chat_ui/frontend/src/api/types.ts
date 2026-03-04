/**
 * TypeScript types mirroring the backend Pydantic models.
 *
 * Every interface here corresponds 1:1 to a Pydantic model in
 * backend/app/models.py. Changes to the backend models MUST be
 * reflected here to maintain type safety.
 */

// ── Enums ───────────────────────────────────────────────────────────────────

export type Role = "system" | "user" | "assistant" | "tool";

export type MessageStatus =
  | "pending"
  | "streaming"
  | "complete"
  | "error"
  | "aborted";

export type StreamEventType =
  | "token"
  | "tool_call_start"
  | "tool_call_delta"
  | "tool_call_end"
  | "tool_result"
  | "thinking"
  | "citation"
  | "error"
  | "done"
  | "aborted"
  | "metadata"
  | "rate_limited"
  | "keepalive";

// ── Tool Calls ──────────────────────────────────────────────────────────────

export type ToolCallStatus = "pending" | "running" | "complete" | "error";

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: string | null;
  status: ToolCallStatus;
  summary?: string;
  duration_ms?: number | null;
  /** Epoch ms when this tool call started — used for live timer. */
  start_ms?: number | null;
}

// ── Content Parts (ordered, interleaved rendering) ──────────────────────────

export type ContentPart =
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "tool_call"; toolCall: ToolCall };

// ── Messages ────────────────────────────────────────────────────────────────

export interface Message {
  id: string;
  role: Role;
  content: string;
  parts: ContentPart[];
  status: MessageStatus;
  tool_calls: ToolCall[];
  context_snapshot?: Record<string, unknown> | null;
  created_at: string;
}

// ── Sessions ────────────────────────────────────────────────────────────────

export interface Session {
  id: string;
  title: string;
  messages: Message[];
  created_at: string;
  updated_at: string;
}

export interface SessionSummary {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

// ── API Requests ────────────────────────────────────────────────────────────

export interface ChatRequest {
  content: string;
  max_context_turns?: number | null;
}

export interface CreateSessionRequest {
  title?: string;
}

export interface UpdateSessionRequest {
  title: string;
}

// ── SSE Events ──────────────────────────────────────────────────────────────

export interface StreamMetadata {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  duration_ms: number;
  model: string;
  assistant_message_id: string;
  estimated_cost_usd?: number | null;
}
