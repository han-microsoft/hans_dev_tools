/**
 * Session store — manages session list and active session.
 *
 * Zustand store with async actions for session CRUD.
 */

import { create } from "zustand";
import type { Session, SessionSummary, Message } from "@/api/types";
import * as api from "@/api/client";

interface SessionState {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  activeSession: Session | null;
  loading: boolean;
  error: string | null;

  fetchSessions: () => Promise<void>;
  createSession: (title?: string) => Promise<Session>;
  selectSession: (sessionId: string) => Promise<void>;
  deleteSession: (sessionId: string) => Promise<void>;
  renameSession: (sessionId: string, title: string) => Promise<void>;
  clearError: () => void;
  refreshActiveSession: () => Promise<void>;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  activeSession: null,
  loading: false,
  error: null,

  fetchSessions: async () => {
    set({ loading: true, error: null });
    try {
      const sessions = await api.listSessions();
      set({ sessions, loading: false });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  createSession: async (title?: string) => {
    try {
      const session = await api.createSession(title ? { title } : undefined);
      set((state) => ({
        sessions: [
          {
            id: session.id,
            title: session.title,
            message_count: 0,
            created_at: session.created_at,
            updated_at: session.updated_at,
          },
          ...state.sessions,
        ],
        activeSessionId: session.id,
        activeSession: session,
      }));
      return session;
    } catch (err) {
      set({ error: (err as Error).message });
      throw err;
    }
  },

  selectSession: async (sessionId: string) => {
    set({ loading: true, error: null, activeSessionId: sessionId });
    try {
      const session = await api.getSession(sessionId);
      set({ activeSession: session, loading: false });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  deleteSession: async (sessionId: string) => {
    try {
      await api.deleteSession(sessionId);
      const { activeSessionId } = get();
      set((state) => ({
        sessions: state.sessions.filter((s) => s.id !== sessionId),
        ...(activeSessionId === sessionId
          ? { activeSessionId: null, activeSession: null }
          : {}),
      }));
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  renameSession: async (sessionId: string, title: string) => {
    try {
      await api.updateSession(sessionId, { title });
      set((state) => ({
        sessions: state.sessions.map((s) =>
          s.id === sessionId ? { ...s, title } : s,
        ),
        activeSession:
          state.activeSession?.id === sessionId
            ? { ...state.activeSession, title }
            : state.activeSession,
      }));
    } catch (err) {
      set({ error: (err as Error).message });
    }
  },

  refreshActiveSession: async () => {
    const { activeSessionId } = get();
    if (!activeSessionId) return;
    try {
      const session = await api.getSession(activeSessionId);
      set({ activeSession: session });
      set((state) => ({
        sessions: state.sessions.map((s) =>
          s.id === activeSessionId
            ? {
                ...s,
                title: session.title,
                message_count: session.messages?.length ?? 0,
                updated_at: session.updated_at,
              }
            : s,
        ),
      }));
    } catch {
      // Silently fail — session may have been deleted
    }
  },

  clearError: () => set({ error: null }),
}));
