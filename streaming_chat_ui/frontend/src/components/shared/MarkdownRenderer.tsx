/**
 * MarkdownRenderer — renders markdown content with syntax highlighting.
 *
 * Features: GFM tables, fenced code blocks with language labels and
 * copy button, styled links, and responsive table wrapping.
 */

import { useState, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Copy, Check } from "lucide-react";

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export function MarkdownRenderer({
  content,
  className: extraClass,
}: MarkdownRendererProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      className={`prose prose-invert max-w-none break-words [font-size:inherit]
        prose-headings:text-[inherit] prose-headings:font-semibold prose-headings:mt-4 prose-headings:mb-2
        prose-p:text-[inherit] prose-p:leading-relaxed
        prose-strong:text-[inherit]
        prose-li:text-[inherit]
        prose-blockquote:border-brand/30 prose-blockquote:text-[inherit]
        ${extraClass ?? ""}`}
      components={{
        code: CodeBlock,
        table: ({ children }) => (
          <div className="my-3 overflow-x-auto rounded-lg border border-border">
            <table className="w-full border-collapse text-[0.85em]">
              {children}
            </table>
          </div>
        ),
        thead: ({ children }) => (
          <thead className="bg-neutral-bg3 text-text-muted text-[0.7em] uppercase tracking-wider">
            {children}
          </thead>
        ),
        th: ({ children }) => (
          <th className="px-3 py-2 text-left font-semibold border-b border-border whitespace-nowrap">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="px-3 py-1.5 text-text-secondary border-b border-border/50 whitespace-nowrap">
            {children}
          </td>
        ),
        tr: ({ children }) => (
          <tr className="transition-colors hover:bg-neutral-bg3/50">
            {children}
          </tr>
        ),
        a: ({ children, href, ...props }) => (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand hover:underline"
            {...props}
          >
            {children}
          </a>
        ),
        pre: ({ children }) => <>{children}</>,
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

function CodeBlock({
  children,
  className,
  ...props
}: React.HTMLAttributes<HTMLElement>) {
  const [copied, setCopied] = useState(false);
  const match = /language-(\w+)/.exec(className ?? "");
  const language = match?.[1] ?? "";
  const code = String(children).replace(/\n$/, "");

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [code]);

  // Inline code (no language)
  if (!match) {
    return (
      <code
        className="rounded bg-neutral-bg3 px-1.5 py-0.5 text-xs font-mono text-brand"
        {...props}
      >
        {children}
      </code>
    );
  }

  // Fenced code block with syntax highlighting
  return (
    <div className="group relative my-3 rounded-lg overflow-hidden border border-border">
      <div className="flex items-center justify-between bg-neutral-bg3 px-3 py-1.5 text-xs text-text-muted">
        <span className="font-mono uppercase">{language}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity hover:text-text-primary"
          aria-label="Copy code"
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5" />
              <span>Copied</span>
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <SyntaxHighlighter
        style={oneDark}
        language={language}
        PreTag="div"
        customStyle={{
          margin: 0,
          borderRadius: 0,
          fontSize: "0.8rem",
          background: "var(--color-bg-2)",
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}
