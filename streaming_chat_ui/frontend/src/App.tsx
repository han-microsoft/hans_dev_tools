/**
 * App — root component with sidebar (session list) + chat panel.
 *
 * Fetches sessions on mount. Provides session CRUD in the sidebar
 * and the full streaming chat experience in the main panel.
 */

import { useEffect } from "react";
import { useSessionStore } from "./stores/sessionStore";
import { useChatStore } from "./stores/chatStore";
import { ChatPanel } from "./components/chat/ChatPanel";
import {
  PlusCircle,
  Trash2,
  MessageSquare,
  Zap,
} from "lucide-react";

export default function App() {
  const sessions = useSessionStore((s) => s.sessions);
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const activeSession = useSessionStore((s) => s.activeSession);
  const fetchSessions = useSessionStore((s) => s.fetchSessions);
  const createSession = useSessionStore((s) => s.createSession);
  const selectSession = useSessionStore((s) => s.selectSession);
  const deleteSession = useSessionStore((s) => s.deleteSession);
  const loadSessionMessages = useChatStore((s) => s.loadSessionMessages);
  const clearChat = useChatStore((s) => s.clearChat);

  // Fetch sessions on mount
  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  // Load messages when active session changes
  useEffect(() => {
    if (activeSession) {
      loadSessionMessages(activeSession.messages ?? []);
    } else {
      clearChat();
    }
  }, [activeSession, loadSessionMessages, clearChat]);

  const handleNewChat = async () => {
    await createSession();
  };

  const handleSelectSession = async (sessionId: string) => {
    if (sessionId === activeSessionId) return;
    await selectSession(sessionId);
  };

  const handleDeleteSession = async (
    e: React.MouseEvent,
    sessionId: string,
  ) => {
    e.stopPropagation();
    await deleteSession(sessionId);
  };

  return (
    <div className="flex h-screen bg-neutral-bg1">
      {/* ── Sidebar ─────────────────────────────────────────────────────── */}
      <aside className="w-64 flex-shrink-0 border-r border-border bg-neutral-bg2 flex flex-col">
        {/* Header */}
        <div className="px-4 py-4 border-b border-border">
          <div className="flex items-center gap-2 mb-3">
            <Zap className="h-5 w-5 text-brand" />
            <h1 className="text-sm font-semibold text-text-primary">
              Streaming Chat UI
            </h1>
          </div>
          <button
            onClick={handleNewChat}
            className="flex w-full items-center gap-2 rounded-lg bg-brand/10 border border-brand/30 px-3 py-2 text-sm font-medium text-brand hover:bg-brand/20 transition-colors"
          >
            <PlusCircle className="h-4 w-4" />
            New Chat
          </button>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto px-2 py-2">
          {sessions.length === 0 ? (
            <p className="px-2 py-4 text-center text-xs text-text-muted">
              No conversations yet.
            </p>
          ) : (
            sessions.map((s) => (
              <button
                key={s.id}
                onClick={() => handleSelectSession(s.id)}
                className={`group flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors mb-0.5 ${
                  s.id === activeSessionId
                    ? "bg-brand/15 text-text-primary"
                    : "text-text-secondary hover:bg-neutral-bg3"
                }`}
              >
                <MessageSquare className="h-3.5 w-3.5 shrink-0 opacity-50" />
                <span className="flex-1 truncate">{s.title}</span>
                <span className="text-[0.65rem] text-text-muted opacity-0 group-hover:opacity-100">
                  {s.message_count}
                </span>
                <Trash2
                  onClick={(e) => handleDeleteSession(e, s.id)}
                  className="h-3.5 w-3.5 shrink-0 text-text-muted opacity-0 group-hover:opacity-100 hover:text-status-error transition-colors"
                />
              </button>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-border text-[0.65rem] text-text-muted">
          Streaming Chat UI v1.0
        </div>
      </aside>

      {/* ── Main panel ──────────────────────────────────────────────────── */}
      <ChatPanel />
    </div>
  );
}
