/**
 * ToolCallDisplay — collapsible tool invocation card.
 *
 * Displays tool name, status indicator, live timer, and expandable
 * arguments/result sections.
 */

import { useState, useRef, useEffect, memo } from "react";
import {
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import type { ToolCall } from "@/api/types";

interface ToolCallDisplayProps {
  toolCall: ToolCall;
  isStreaming?: boolean;
}

/** Tool name → emoji icon mapping for common tools. */
const TOOL_ICONS: Record<string, string> = {
  thinking: "💭",
  search_documentation: "🔍",
  read_file: "📄",
  create_file: "📝",
  run_command: "⚡",
};

export const ToolCallDisplay = memo(function ToolCallDisplay({
  toolCall,
  isStreaming = false,
}: ToolCallDisplayProps) {
  const [expanded, setExpanded] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const icon = TOOL_ICONS[toolCall.name] ?? "🔧";
  const hasResult = !!toolCall.result;
  const isRunning = toolCall.status === "running" || (isStreaming && !hasResult);
  const isError = toolCall.status === "error";
  const hasArgs = Object.keys(toolCall.arguments).length > 0;

  // Live timer for running tool calls
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (isRunning && !toolCall.duration_ms) {
      const start = toolCall.start_ms ?? Date.now();
      setElapsedMs(Date.now() - start);
      intervalRef.current = setInterval(
        () => setElapsedMs(Date.now() - start),
        100,
      );
      return () => {
        if (intervalRef.current) clearInterval(intervalRef.current);
      };
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [isRunning, toolCall.duration_ms, toolCall.start_ms]);

  const displayDuration =
    toolCall.duration_ms ?? (isRunning ? elapsedMs : null);

  return (
    <div className="my-1 rounded-lg border border-border bg-neutral-bg2 overflow-hidden transition-all [font-size:inherit]">
      {/* Header row */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 hover:bg-neutral-bg3 transition-colors text-left"
      >
        <span className="shrink-0">{icon}</span>
        <span className="font-mono font-medium text-text-primary truncate">
          {toolCall.name}
        </span>

        {/* Duration timer */}
        {displayDuration != null && (
          <span className="text-[0.8em] font-mono text-text-muted tabular-nums">
            {(displayDuration / 1000).toFixed(1)}s
          </span>
        )}

        {/* Status indicator */}
        {isRunning && (
          <span className="ml-auto flex items-center gap-1.5 text-[0.85em] text-brand">
            <span className="h-1.5 w-1.5 rounded-full bg-brand animate-pulse" />
            Running…
          </span>
        )}
        {hasResult && !isError && (
          <>
            <CheckCircle2 className="h-3.5 w-3.5 text-status-success shrink-0 ml-auto" />
            {toolCall.summary && (
              <span className="text-[0.85em] text-text-muted truncate max-w-[200px]">
                {toolCall.summary}
              </span>
            )}
          </>
        )}
        {isError && (
          <>
            <XCircle className="h-3.5 w-3.5 text-status-error shrink-0 ml-auto" />
            {toolCall.summary && (
              <span className="text-[0.85em] text-status-error truncate max-w-[200px]">
                {toolCall.summary}
              </span>
            )}
          </>
        )}

        {/* Chevron */}
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-text-muted shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-text-muted shrink-0" />
        )}
      </button>

      {/* Expandable detail */}
      {expanded && (
        <div className="border-t border-border px-3 py-2 space-y-2">
          {/* Arguments */}
          {hasArgs && (
            <div>
              <span className="text-[0.7em] font-medium text-text-muted uppercase tracking-wider">
                Arguments
              </span>
              <pre className="mt-1 rounded bg-neutral-bg1 p-2 text-xs font-mono text-text-secondary overflow-x-auto">
                {JSON.stringify(toolCall.arguments, null, 2)}
              </pre>
            </div>
          )}

          {/* Result */}
          {hasResult && (
            <div>
              <span
                className={`text-[0.7em] font-medium uppercase tracking-wider ${
                  isError ? "text-status-error" : "text-status-success"
                }`}
              >
                Result
              </span>
              <pre className="mt-1 rounded bg-neutral-bg1 p-2 text-xs font-mono text-text-secondary overflow-x-auto max-h-64 overflow-y-auto">
                {(() => {
                  try {
                    return JSON.stringify(
                      JSON.parse(toolCall.result!),
                      null,
                      2,
                    );
                  } catch {
                    return toolCall.result;
                  }
                })()}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
});
