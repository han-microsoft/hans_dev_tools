/**
 * MessageBubble — renders a single user or assistant message.
 *
 * User messages: right-aligned plain text bubbles with markdown.
 * Assistant messages: ordered ContentPart array (thinking → tool → text).
 * During streaming: renders streamingParts instead of persisted parts.
 */

import { useState, memo } from "react";
import {
  Bot,
  User,
  AlertCircle,
  StopCircle,
  ChevronRight,
} from "lucide-react";
import type { Message, ContentPart } from "@/api/types";
import { legacyToParts } from "@/features/chat/partUtils";
import { MarkdownRenderer } from "../shared/MarkdownRenderer";
import { ToolCallDisplay } from "./ToolCallDisplay";
import { ThinkingBlock } from "./ThinkingBlock";
import { TextBlock } from "./TextBlock";
import { StreamingIndicator } from "./StreamingIndicator";

interface MessageBubbleProps {
  message: Message;
  streamingParts?: ContentPart[];
  isStreaming?: boolean;
}

export const MessageBubble = memo(function MessageBubble({
  message,
  streamingParts,
  isStreaming = false,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  const isError = message.status === "error";
  const isAborted = message.status === "aborted";
  const [collapsed, setCollapsed] = useState(false);

  // Determine parts to render
  const parts: ContentPart[] = (
    isStreaming
      ? streamingParts ?? []
      : message.parts?.length
        ? message.parts
        : legacyToParts(message)
  ).filter(
    (p): p is ContentPart => p != null && typeof p === "object" && "type" in p,
  );

  const timestamp = new Date(message.created_at).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  const toggleCollapse = (e: React.MouseEvent) => {
    e.stopPropagation();
    setCollapsed((v) => !v);
  };

  return (
    <article
      className={`flex gap-3 px-4 py-3 group/msg ${
        isUser ? "flex-row-reverse" : "flex-row"
      }`}
      aria-label={`${message.role} message at ${timestamp}`}
    >
      {/* Avatar */}
      <div className="flex flex-col items-center shrink-0 gap-0.5">
        <div
          onClick={toggleCollapse}
          className={`flex h-8 w-8 items-center justify-center rounded-full cursor-pointer ${
            isUser
              ? "bg-brand text-white"
              : "bg-neutral-bg3 text-text-secondary"
          }`}
          title={collapsed ? "Click to expand" : "Click to collapse"}
        >
          {isUser ? (
            <User className="h-4 w-4" />
          ) : (
            <Bot className="h-4 w-4" />
          )}
        </div>
      </div>

      {/* Collapse chevron */}
      <ChevronRight
        onClick={toggleCollapse}
        className={`h-7 w-7 shrink-0 self-center text-text-primary opacity-70 hover:opacity-100 transition-all cursor-pointer ${
          collapsed ? "" : "rotate-90"
        }`}
      />

      {/* Content */}
      <div
        className={`flex max-w-[80%] flex-col gap-1 ${
          isUser ? "items-end" : "items-start"
        }`}
      >
        {/* User message */}
        {isUser && (
          <div className="rounded-2xl px-4 py-2.5 bg-brand text-white rounded-br-md">
            {collapsed ? (
              <p className="truncate max-w-[60ch]">{message.content}</p>
            ) : (
              <MarkdownRenderer
                content={message.content ?? ""}
                className="text-white prose-headings:!text-white prose-p:!text-white prose-strong:!text-white prose-li:!text-white"
              />
            )}
          </div>
        )}

        {/* Assistant message — interleaved parts */}
        {!isUser && (
          <div className={`w-full ${collapsed ? "" : "space-y-1"}`}>
            {/* Status indicators */}
            {isError && (
              <div className="flex items-center gap-1.5 text-status-error text-xs px-1">
                <AlertCircle className="h-3.5 w-3.5" />
                <span>Error generating response</span>
              </div>
            )}
            {isAborted && (
              <div className="flex items-center gap-1.5 text-status-warning text-xs px-1">
                <StopCircle className="h-3.5 w-3.5" />
                <span>Generation stopped</span>
              </div>
            )}

            {/* Collapsed preview */}
            {collapsed ? (
              <div className="rounded-2xl bg-neutral-bg2 text-text-primary rounded-bl-md px-4 py-2 truncate max-w-full">
                {(() => {
                  const firstText = parts.find((p) => p.type === "text");
                  if (firstText && firstText.type === "text") {
                    const preview = firstText.text
                      .replace(/\n/g, " ")
                      .slice(0, 120);
                    return (
                      <span className="text-text-secondary">
                        {preview}
                        {firstText.text.length > 120 ? "…" : ""}
                      </span>
                    );
                  }
                  return (
                    <span className="text-text-muted italic">collapsed</span>
                  );
                })()}
              </div>
            ) : (
              <>
                {/* Render parts in order with chain connectors */}
                {parts.flatMap((part, i) => {
                  const nextPart =
                    i < parts.length - 1 ? parts[i + 1] : null;
                  const nextIsChainable =
                    nextPart?.type === "tool_call" ||
                    nextPart?.type === "thinking";

                  const rendered = (() => {
                    switch (part.type) {
                      case "text":
                        return (
                          <TextBlock
                            key={`text-${i}`}
                            text={part.text}
                            isStreaming={isStreaming}
                          />
                        );
                      case "thinking":
                        return (
                          <ThinkingBlock key={`think-${i}`} text={part.text} />
                        );
                      case "tool_call":
                        return (
                          <ToolCallDisplay
                            key={part.toolCall.id}
                            toolCall={part.toolCall}
                            isStreaming={isStreaming}
                          />
                        );
                    }
                  })();

                  /* Chain connector between tool calls */
                  if (part.type === "tool_call" && nextIsChainable) {
                    return [
                      rendered,
                      <div
                        key={`connector-${i}`}
                        className="ml-3 flex flex-col items-center w-3"
                      >
                        <div className="h-4 w-0 border-l-2 border-text-muted/30" />
                        <div className="h-2.5 w-2.5 rounded-full border-2 border-text-muted/30" />
                      </div>,
                    ];
                  }
                  return [rendered];
                })}

                {/* Streaming indicator when no parts yet */}
                {isStreaming && parts.length === 0 && (
                  <div className="rounded-2xl bg-neutral-bg2 text-text-primary rounded-bl-md px-4 py-2.5">
                    <StreamingIndicator />
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* Timestamp */}
        <span
          onClick={toggleCollapse}
          className="px-2 text-xs text-text-muted cursor-pointer hover:text-text-secondary transition-colors"
        >
          {timestamp}
        </span>
      </div>
    </article>
  );
});
