/**
 * API client — typed fetch helpers + error handling.
 *
 * The sole module that makes HTTP requests to the backend.
 */

import { BASE } from "@/foundation/constants";
import type {
  CreateSessionRequest,
  Session,
  SessionSummary,
  UpdateSessionRequest,
} from "./types";

export { BASE };

// ── Error ───────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }
  return res.json();
}

// ── Session CRUD ────────────────────────────────────────────────────────────

export async function createSession(
  req?: CreateSessionRequest,
): Promise<Session> {
  const res = await fetch(`${BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req ?? {}),
  });
  return handleResponse<Session>(res);
}

export async function listSessions(): Promise<SessionSummary[]> {
  const res = await fetch(`${BASE}/sessions`);
  return handleResponse<SessionSummary[]>(res);
}

export async function getSession(sessionId: string): Promise<Session> {
  const res = await fetch(`${BASE}/sessions/${sessionId}`);
  return handleResponse<Session>(res);
}

export async function updateSession(
  sessionId: string,
  req: UpdateSessionRequest,
): Promise<Session> {
  const res = await fetch(`${BASE}/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handleResponse<Session>(res);
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }
}

// ── Re-export chat API ──────────────────────────────────────────────────────
export * from "./chatApi";
