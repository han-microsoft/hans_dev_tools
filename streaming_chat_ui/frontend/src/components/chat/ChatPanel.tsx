/**
 * ChatPanel — main chat container composing MessageList + ChatInput.
 *
 * Surfaces error banner and metadata bar. No props — all state
 * consumed from Zustand stores.
 */

import { useChatStore } from "@/stores/chatStore";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";

export function ChatPanel() {
  const error = useChatStore((s) => s.error);
  const lastMetadata = useChatStore((s) => s.lastMetadata);

  return (
    <main className="flex flex-1 flex-col bg-neutral-bg1 overflow-hidden">
      {/* Error banner */}
      {error && (
        <div className="border-b border-status-error/30 bg-status-error/10 px-4 py-2 text-sm text-status-error">
          {error}
        </div>
      )}

      {/* Messages */}
      <MessageList />

      {/* Metadata bar — shown after completed assistant turn */}
      {lastMetadata && (
        <div className="border-t border-border bg-neutral-bg2 px-4 py-1.5 flex items-center gap-4 text-xs text-text-muted">
          <span>Model: {lastMetadata.model}</span>
          <span>Tokens: {lastMetadata.total_tokens.toLocaleString()}</span>
          <span>
            Duration: {(lastMetadata.duration_ms / 1000).toFixed(1)}s
          </span>
          {lastMetadata.estimated_cost_usd != null && (
            <span>Cost: ${lastMetadata.estimated_cost_usd.toFixed(4)}</span>
          )}
        </div>
      )}

      {/* Input */}
      <ChatInput />
    </main>
  );
}
