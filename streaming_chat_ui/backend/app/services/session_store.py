"""In-memory session store — dict-backed, zero external dependencies.

Provides session CRUD and message persistence for development and
single-process deployments. For production, swap with a database-backed
implementation that conforms to the same async interface.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.models import Message, MessageStatus, Session, SessionSummary

logger = logging.getLogger(__name__)


class InMemorySessionStore:
    """Thread-safe in-memory session storage."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    # ── CRUD ─────────────────────────────────────────────────────────────

    async def create(self, session: Session) -> Session:
        """Persist a new session."""
        async with self._lock:
            self._sessions[session.id] = session
        return session

    async def get(self, session_id: str) -> Session | None:
        """Retrieve a session by ID, or None if not found."""
        async with self._lock:
            return self._sessions.get(session_id)

    async def list_all(self) -> list[SessionSummary]:
        """List all sessions as lightweight summaries, newest first."""
        async with self._lock:
            snapshot = list(self._sessions.values())
        summaries = [
            SessionSummary(
                id=s.id,
                title=s.title,
                message_count=len(s.messages),
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in snapshot
        ]
        return sorted(summaries, key=lambda s: s.updated_at, reverse=True)

    async def update(self, session: Session) -> Session:
        """Replace a session in the store (updates updated_at)."""
        async with self._lock:
            session.updated_at = datetime.now(timezone.utc)
            self._sessions[session.id] = session
        return session

    async def delete(self, session_id: str) -> bool:
        """Remove a session. Returns True if it existed."""
        async with self._lock:
            return self._sessions.pop(session_id, None) is not None

    # ── Message operations ───────────────────────────────────────────────

    async def append_message(self, session_id: str, message: Message) -> None:
        """Append a message to a session's message list."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(f"Session {session_id} not found")
            session.messages.append(message)
            session.updated_at = datetime.now(timezone.utc)

    async def update_message(self, session_id: str, message: Message) -> None:
        """Update an existing message in a session (matched by id)."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(f"Session {session_id} not found")
            for i, m in enumerate(session.messages):
                if m.id == message.id:
                    session.messages[i] = message
                    session.updated_at = datetime.now(timezone.utc)
                    return
