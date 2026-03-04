/**
 * Chat part utilities — pure functions for content part transformations.
 *
 * Functions:
 *   - legacyToParts() — converts old messages (no parts array) to ContentPart[]
 *   - generateToolSummary() — produces a one-line summary from tool result JSON
 */

import type { Message, ContentPart, ToolCall } from "@/api/types";

/**
 * Generate a one-line summary from a tool's result JSON.
 *
 * Recognizes common response shapes:
 *   - error responses → "✗ <detail>"
 *   - tabular (columns + data/rows) → "✓ N rows"
 *   - search results → "✓ N results"
 *   - other JSON → "✓ Complete"
 *   - non-JSON → truncated raw string
 */
export function generateToolSummary(_name: string, result: string): string {
  try {
    const parsed = JSON.parse(result);
    if (parsed.error) {
      const detail = parsed.detail ?? "Error";
      return `✗ ${typeof detail === "string" ? detail.slice(0, 80) : "Error"}`;
    }
    if (parsed.columns && parsed.data) return `✓ ${parsed.data.length} rows`;
    if (parsed.columns && parsed.rows) return `✓ ${parsed.rows.length} rows`;
    if (parsed.results)
      return `✓ ${parsed.count ?? parsed.results.length} results`;
    return "✓ Complete";
  } catch {
    return result.length > 60 ? result.slice(0, 60) + "…" : result;
  }
}

/**
 * Reconstruct ContentPart[] from a message that lacks a parts array.
 *
 * Tool calls appear first, then text content — preserving chronological order.
 */
export function legacyToParts(msg: Message): ContentPart[] {
  const parts: ContentPart[] = [];
  const toolCalls = msg.tool_calls ?? [];
  const content = msg.content ?? "";

  /* Tool call parts first */
  for (const tc of toolCalls) {
    parts.push({
      type: "tool_call",
      toolCall: {
        ...tc,
        status: tc.result ? "complete" : "pending",
      } as ToolCall,
    });
  }

  /* Text content last */
  if (content) {
    const trimmed = content.trim();
    if (trimmed) parts.push({ type: "text", text: trimmed });
  }

  return parts;
}
