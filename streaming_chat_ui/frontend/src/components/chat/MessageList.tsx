/**
 * MessageList — scrollable message timeline with auto-scroll.
 *
 * Renders all persisted messages via MessageBubble, plus a streaming
 * placeholder during active generation. Shows empty state when no
 * session is active.
 */

import { ArrowDown, MessageSquare, PlusCircle } from "lucide-react";
import { useChatStore } from "@/stores/chatStore";
import { useSessionStore } from "@/stores/sessionStore";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { MessageBubble } from "./MessageBubble";

export function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const status = useChatStore((s) => s.status);
  const streamingParts = useChatStore((s) => s.streamingParts);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const createSession = useSessionStore((s) => s.createSession);

  const { containerRef, handleScroll, scrollToBottom, showScrollButton } =
    useAutoScroll({
      threshold: 120,
      deps: [streamingParts.length, messages.length],
    });

  // Empty state
  if (messages.length === 0 && status === "idle") {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 text-text-muted">
        <MessageSquare className="h-12 w-12 opacity-30" />
        <div className="text-center">
          <p className="text-lg font-medium">
            {activeSessionId
              ? "Start a conversation"
              : "No chat session loaded"}
          </p>
          <p className="text-sm mt-1">
            {activeSessionId
              ? "Type a message below to begin."
              : "Create a new session to get started."}
          </p>
        </div>
        {!activeSessionId && (
          <button
            onClick={() => createSession()}
            className="flex items-center gap-2 mt-2 px-4 py-2 rounded-lg bg-brand/10 border border-brand/30 text-brand text-sm font-medium hover:bg-brand/20 transition-colors"
          >
            <PlusCircle className="h-4 w-4" />
            New Chat
          </button>
        )}
      </div>
    );
  }

  const isStreaming = status === "streaming";

  return (
    <div className="relative flex-1 overflow-hidden">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="h-full overflow-y-auto scroll-smooth"
      >
        <div className="mx-auto px-6 py-4">
          {messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))}

          {/* Streaming placeholder */}
          {isStreaming && (
            <MessageBubble
              message={{
                id: "streaming",
                role: "assistant",
                content: "",
                parts: [],
                status: "streaming",
                tool_calls: [],
                created_at: new Date().toISOString(),
              }}
              streamingParts={streamingParts}
              isStreaming
            />
          )}
        </div>
      </div>

      {/* Scroll to bottom button */}
      {showScrollButton && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-4 right-4 flex items-center gap-1.5 rounded-full bg-neutral-bg3 px-3 py-1.5 text-xs text-text-secondary shadow-lg border border-border hover:bg-neutral-bg4 transition-colors"
          aria-label="Scroll to bottom"
        >
          <ArrowDown className="h-3.5 w-3.5" />
          <span>New messages</span>
        </button>
      )}
    </div>
  );
}
