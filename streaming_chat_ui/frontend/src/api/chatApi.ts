/**
 * Chat API — SSE streaming and abort.
 *
 * Uses fetch + ReadableStream (not EventSource) for:
 *   - POST method support (EventSource is GET-only)
 *   - AbortController integration
 *   - Custom event type parsing
 */

import type { ChatRequest, StreamEventType } from "./types";
import { BASE } from "./client";

export interface SSECallbacks {
  onToken?: (token: string) => void;
  onToolCallStart?: (id: string, name: string) => void;
  onToolCallDelta?: (id: string, argumentsDelta: string) => void;
  onToolCallEnd?: (
    id: string,
    name: string,
    args: Record<string, unknown>,
  ) => void;
  onToolResult?: (id: string, name: string, result: string) => void;
  onThinking?: (title: string, detail: string) => void;
  onMetadata?: (data: Record<string, unknown>) => void;
  onRateLimited?: (retryAfter: number, attempt: number) => void;
  onKeepalive?: () => void;
  onError?: (error: string, errorCode?: string, errorId?: string) => void;
  onDone?: () => void;
  onAborted?: () => void;
}

/**
 * Send a chat message and consume the SSE response stream.
 *
 * Manual SSE frame parsing: splits on \n\n, extracts event: and data: lines.
 * Type-safe callback dispatch: switch on eventType → typed callbacks.
 */
export async function streamChat(
  sessionId: string,
  req: ChatRequest,
  callbacks: SSECallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${BASE}/chat/${sessionId}`;

  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(req),
      signal,
    });
  } catch (fetchErr) {
    callbacks.onError?.((fetchErr as Error).message);
    return;
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    callbacks.onError?.(body.detail ?? `HTTP ${res.status}`);
    return;
  }

  const reader = res.body?.getReader();
  if (!reader) {
    callbacks.onError?.("No response body");
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      // Decode chunk and normalise line endings
      const chunk = decoder.decode(value, { stream: true });
      buffer += chunk.replace(/\r\n/g, "\n");

      // Split into SSE frames (double newline separated)
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        if (!frame.trim()) continue;

        let eventType: StreamEventType | null = null;
        const dataLines: string[] = [];

        // Parse event: and data: lines
        for (const line of frame.split("\n")) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim() as StreamEventType;
          } else if (line.startsWith("data: ")) {
            dataLines.push(line.slice(6));
          }
        }

        const data = dataLines.join("\n");
        if (!eventType) continue;

        let parsed: Record<string, unknown> = {};
        try {
          parsed = data ? JSON.parse(data) : {};
        } catch {
          console.warn(
            "[streamChat] Non-JSON SSE data skipped:",
            data?.slice(0, 100),
          );
        }

        // Dispatch to typed callbacks
        switch (eventType) {
          case "token":
            callbacks.onToken?.(parsed.token as string);
            break;
          case "tool_call_start":
            callbacks.onToolCallStart?.(
              parsed.id as string,
              parsed.name as string,
            );
            break;
          case "tool_call_delta":
            callbacks.onToolCallDelta?.(
              parsed.id as string,
              parsed.arguments_delta as string,
            );
            break;
          case "tool_call_end":
            callbacks.onToolCallEnd?.(
              parsed.id as string,
              parsed.name as string,
              parsed.arguments as Record<string, unknown>,
            );
            break;
          case "tool_result":
            callbacks.onToolResult?.(
              parsed.id as string,
              parsed.name as string,
              parsed.result as string,
            );
            break;
          case "thinking":
            callbacks.onThinking?.(
              parsed.title as string,
              parsed.detail as string,
            );
            break;
          case "metadata":
            callbacks.onMetadata?.(parsed);
            break;
          case "rate_limited":
            callbacks.onRateLimited?.(
              parsed.retry_after as number,
              parsed.attempt as number,
            );
            break;
          case "error":
            callbacks.onError?.(
              parsed.error as string,
              parsed.error_code as string | undefined,
              parsed.error_id as string | undefined,
            );
            break;
          case "done":
            callbacks.onDone?.();
            break;
          case "aborted":
            callbacks.onAborted?.();
            break;
          case "keepalive":
            callbacks.onKeepalive?.();
            break;
        }
      }
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") {
      callbacks.onAborted?.();
    } else {
      callbacks.onError?.((err as Error).message);
    }
  }
}

/** Send an abort request to cancel an in-flight generation. */
export async function abortGeneration(sessionId: string): Promise<void> {
  await fetch(`${BASE}/chat/${sessionId}/abort`, { method: "POST" });
}
