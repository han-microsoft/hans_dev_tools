/**
 * StreamingIndicator — animated typing dots shown while assistant is generating.
 */

export function StreamingIndicator() {
  return (
    <div
      className="flex items-center gap-1 px-4 py-2"
      role="status"
      aria-label="Assistant is typing"
    >
      <div
        className="h-2 w-2 rounded-full bg-brand animate-pulse-dot"
        style={{ animationDelay: "0s" }}
      />
      <div
        className="h-2 w-2 rounded-full bg-brand animate-pulse-dot"
        style={{ animationDelay: "0.2s" }}
      />
      <div
        className="h-2 w-2 rounded-full bg-brand animate-pulse-dot"
        style={{ animationDelay: "0.4s" }}
      />
    </div>
  );
}
