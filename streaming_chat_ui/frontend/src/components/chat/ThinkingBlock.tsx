/**
 * ThinkingBlock — renders agent reasoning steps with a thought-bubble icon.
 *
 * Visually distinct: muted colour, italic, left border accent.
 */

import { MarkdownRenderer } from "../shared/MarkdownRenderer";

interface ThinkingBlockProps {
  text: string;
}

export function ThinkingBlock({ text }: ThinkingBlockProps) {
  if (!text.trim()) return null;

  return (
    <div className="flex gap-2 border-l-2 border-brand/30 rounded-md bg-neutral-bg2/60 pl-3 pr-3 py-2 my-1">
      <span className="shrink-0">💭</span>
      <div className="text-text-muted italic">
        <MarkdownRenderer content={text} />
      </div>
    </div>
  );
}
