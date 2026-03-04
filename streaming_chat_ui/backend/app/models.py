"""Pydantic models — the API contract between frontend and backend.

Defines every data shape that crosses the wire. The frontend TypeScript
types in api/types.ts mirror these models exactly.

Model groups:
    Enums       — Role, MessageStatus, StreamEventType
    Core Models — ToolCall, Message, Session, SessionSummary
    API I/O     — ChatRequest, CreateSessionRequest, UpdateSessionRequest
    SSE Events  — StreamEvent, StreamMetadata
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────


class Role(str, Enum):
    """Message author role — mirrors OpenAI chat completion roles."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessageStatus(str, Enum):
    """Lifecycle state of a message."""
    PENDING = "pending"
    STREAMING = "streaming"
    COMPLETE = "complete"
    ERROR = "error"
    ABORTED = "aborted"


class StreamEventType(str, Enum):
    """SSE event types emitted during chat streaming."""
    TOKEN = "token"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_END = "tool_call_end"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    CITATION = "citation"
    ERROR = "error"
    DONE = "done"
    ABORTED = "aborted"
    METADATA = "metadata"
    RATE_LIMITED = "rate_limited"
    KEEPALIVE = "keepalive"


# ── Tool Calls ───────────────────────────────────────────────────────────────


class ToolCall(BaseModel):
    """A single tool invocation within an assistant message."""
    id: str = Field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str | None = None
    duration_ms: float | None = None


# ── Context Snapshot ─────────────────────────────────────────────────────────


class ContextSnapshot(BaseModel):
    """Audit record of exactly what context was sent to the LLM."""
    agent_id: str = ""
    system_prompt_chars: int = 0
    messages_total: int = 0
    messages_kept: int = 0
    messages_dropped: int = 0
    tokens_used: int = 0
    tokens_budget: int = 0
    max_turns: int | None = None
    user_message: str = ""
    model: str = ""
    duration_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None


# ── Messages ─────────────────────────────────────────────────────────────────


class Message(BaseModel):
    """A single message in a conversation thread."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    role: Role
    content: str = ""
    status: MessageStatus = MessageStatus.COMPLETE
    tool_calls: list[ToolCall] = Field(default_factory=list)
    context_snapshot: ContextSnapshot | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


# ── Sessions ─────────────────────────────────────────────────────────────────


class Session(BaseModel):
    """A conversation session containing a list of messages."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    title: str = "New conversation"
    messages: list[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SessionSummary(BaseModel):
    """Lightweight session info for sidebar listing (no messages)."""
    id: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


# ── API Requests ─────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    """Client → Server: send a chat message."""
    content: str = Field(..., min_length=1, max_length=100_000)
    max_context_turns: int | None = None


class CreateSessionRequest(BaseModel):
    """Client → Server: create a new session."""
    title: str = "New conversation"


class UpdateSessionRequest(BaseModel):
    """Client → Server: rename a session."""
    title: str


# ── SSE Events ───────────────────────────────────────────────────────────────


class StreamEvent(BaseModel):
    """A single SSE event emitted during chat streaming."""
    event: StreamEventType
    data: dict[str, Any] = Field(default_factory=dict)


class StreamMetadata(BaseModel):
    """Token usage and timing metadata emitted with the DONE event."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_ms: float = 0.0
    model: str = ""
    assistant_message_id: str = ""
    estimated_cost_usd: float | None = None
