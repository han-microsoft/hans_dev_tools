"""Session router — CRUD endpoints for conversation sessions.

Provides session creation, listing, retrieval, update, and deletion.

Dependents:
    Mounted by main.py under /api/sessions.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.models import (
    CreateSessionRequest,
    Session,
    SessionSummary,
    UpdateSessionRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=Session)
async def create_session(
    request: Request,
    req: CreateSessionRequest | None = None,
):
    """Create a new conversation session."""
    store = request.app.state.store
    session = Session(title=req.title if req else "New conversation")
    created = await store.create(session)
    logger.info("session.created", extra={"session_id": created.id, "title": created.title})
    return created


@router.get("", response_model=list[SessionSummary])
async def list_sessions(request: Request):
    """List all sessions as lightweight summaries (newest first)."""
    store = request.app.state.store
    return await store.list_all()


@router.get("/{session_id}", response_model=Session)
async def get_session(session_id: str, request: Request):
    """Retrieve a session with its full message history."""
    store = request.app.state.store
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.patch("/{session_id}", response_model=Session)
async def update_session(
    session_id: str,
    req: UpdateSessionRequest,
    request: Request,
):
    """Rename a session."""
    store = request.app.state.store
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.title = req.title
    updated = await store.update(session)
    return updated


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    """Delete a session."""
    store = request.app.state.store
    deleted = await store.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}
