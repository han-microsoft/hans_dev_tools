/**
 * Message builder — assembles final Message objects from streaming ContentParts.
 *
 * Pure function called by chatStore onDone/onAborted/onError callbacks.
 */

import type { ContentPart, Message, ToolCall } from "@/api/types";

/**
 * Build a final assistant Message from accumulated streaming parts.
 *
 * Extracts text-type parts and joins them as content (double newline
 * separator). Extracts tool_call parts into the tool_calls array.
 * The full parts array is preserved for interleaved rendering.
 */
export function buildAssistantMessage(
  streamingParts: ContentPart[],
  status: "complete" | "aborted" | "error",
): Message {
  /* Extract text content */
  const textParts = streamingParts
    .filter(
      (p): p is Extract<ContentPart, { type: "text" }> => p.type === "text",
    )
    .map((p) => p.text);

  /* Extract tool calls for backward compat */
  const toolCalls: ToolCall[] = streamingParts
    .filter(
      (p): p is Extract<ContentPart, { type: "tool_call" }> =>
        p.type === "tool_call",
    )
    .map((p) => p.toolCall);

  /* ID prefix distinguishes aborted messages in the ID sync logic */
  const prefix = status === "aborted" ? "aborted" : "assistant";

  return {
    id: `${prefix}-${Date.now()}`,
    role: "assistant",
    content: textParts.join("\n\n"),
    parts: streamingParts,
    status,
    tool_calls: toolCalls,
    created_at: new Date().toISOString(),
  };
}
