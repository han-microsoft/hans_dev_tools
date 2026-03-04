/**
 * TextBlock — renders a text ContentPart with markdown rendering.
 *
 * During streaming, markdown rendering is debounced to at most once
 * per 150ms to avoid CPU-heavy re-parses on every token.
 */

import { useState, useEffect, useRef } from "react";
import { MarkdownRenderer } from "../shared/MarkdownRenderer";

interface TextBlockProps {
  text: string;
  isStreaming?: boolean;
}

/** Debounce interval for markdown rendering during streaming (ms). */
const RENDER_DEBOUNCE_MS = 150;

export function TextBlock({ text, isStreaming = false }: TextBlockProps) {
  const [rendered, setRendered] = useState(text);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!isStreaming) {
      // Not streaming — render immediately
      setRendered(text);
      return;
    }
    // Streaming — debounce markdown re-parse
    if (!timerRef.current) {
      timerRef.current = setTimeout(() => {
        setRendered(text);
        timerRef.current = null;
      }, RENDER_DEBOUNCE_MS);
    }
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [text, isStreaming]);

  // Flush final content when streaming ends
  useEffect(() => {
    if (!isStreaming) {
      setRendered(text);
    }
  }, [isStreaming, text]);

  if (!rendered) return null;

  return (
    <div className="rounded-2xl bg-neutral-bg2 text-text-primary rounded-bl-md px-4 py-2.5">
      <MarkdownRenderer content={rendered} />
    </div>
  );
}
