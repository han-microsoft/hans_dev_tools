/**
 * ChatInput — auto-resizing textarea with send and abort controls.
 *
 * Enter submits, Shift+Enter inserts newline. During streaming,
 * the send button becomes a red abort button.
 */

import { useCallback, useRef, useState, KeyboardEvent, FormEvent } from "react";
import { Send, Square } from "lucide-react";
import { useChatStore } from "@/stores/chatStore";
import { useSessionStore } from "@/stores/sessionStore";

export function ChatInput() {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const status = useChatStore((s) => s.status);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const abort = useChatStore((s) => s.abort);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);

  const isStreaming = status === "streaming";

  const handleSubmit = useCallback(
    async (e?: FormEvent) => {
      e?.preventDefault();
      if (!activeSessionId || isStreaming) return;

      const content = textareaRef.current?.value.trim();
      if (!content) return;

      // Clear input immediately (optimistic)
      if (textareaRef.current) {
        textareaRef.current.value = "";
        textareaRef.current.style.height = "auto";
      }

      await sendMessage(activeSessionId, content);
    },
    [activeSessionId, isStreaming, sendMessage],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  const handleInput = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  const handleAbort = useCallback(() => {
    if (activeSessionId) {
      abort(activeSessionId);
    }
  }, [activeSessionId, abort]);

  if (!activeSessionId) {
    return (
      <div className="border-t border-border bg-neutral-bg1 px-4 py-4">
        <p className="text-center text-sm text-text-muted">
          Select or create a conversation to start chatting.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="bg-neutral-bg1 px-4 py-3">
      <div className="mx-auto flex items-center gap-2 px-2">
        {/* Textarea */}
        <div
          className={`flex-1 rounded-2xl border transition-colors ${
            isStreaming
              ? "border-white/10 bg-neutral-bg3 opacity-60"
              : "border-white/10 bg-neutral-bg2 focus-within:border-white/25"
          }`}
        >
          <textarea
            ref={textareaRef}
            onKeyDown={handleKeyDown}
            onInput={handleInput}
            placeholder="Send a message…"
            rows={1}
            disabled={isStreaming}
            className="w-full resize-none rounded-2xl bg-transparent px-4 py-3 text-sm text-text-primary placeholder-text-muted outline-none disabled:opacity-50"
            aria-label="Chat message input"
          />
        </div>

        {/* Send / Abort button */}
        {isStreaming ? (
          <button
            type="button"
            onClick={handleAbort}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-status-error/20 text-status-error hover:bg-status-error/30 transition-colors"
            aria-label="Stop generating"
          >
            <Square className="h-4 w-4 fill-current" />
          </button>
        ) : (
          <button
            type="submit"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand text-white hover:bg-brand-hover transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            aria-label="Send message"
          >
            <Send className="h-4 w-4" />
          </button>
        )}
      </div>
    </form>
  );
}
