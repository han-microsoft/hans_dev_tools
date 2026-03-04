"""Chat router — SSE streaming endpoint with keepalive and abort support.

Core streaming flow:
    1. Validate session exists
    2. Build token-budgeted context window
    3. Stream from LLM via keepalive_wrap()
    4. Yield typed SSE events (token, tool_call_*, metadata, done)
    5. Persist messages to session store

Dependents:
    Mounted by main.py under /api/chat.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.models import (
    ChatRequest,
    Message,
    MessageStatus,
    Role,
    StreamEvent,
    StreamEventType,
)
from app.services.context import build_context_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# ── Constants ────────────────────────────────────────────────────────────────

# Total timeout for a single chat stream (covers cold starts + long tool use)
CHAT_TIMEOUT_SECONDS = 300

# Interval between keepalive events to prevent proxy/browser timeout
KEEPALIVE_INTERVAL_SECONDS = 15

# In-flight abort events keyed by session_id
_abort_events: dict[str, asyncio.Event] = {}


# ── Keepalive wrapper ───────────────────────────────────────────────────────


async def _keepalive_wrap(
    inner: AsyncIterator[StreamEvent],
    interval: float = KEEPALIVE_INTERVAL_SECONDS,
    abort_event: asyncio.Event | None = None,
) -> AsyncIterator[StreamEvent]:
    """Wrap an inner stream with keepalive events and abort monitoring.

    Injects KEEPALIVE events during silence periods to prevent
    proxy/browser timeout. Monitors the abort_event and yields
    an ABORTED event if it is set.

    Args:
        inner: The underlying LLM stream.
        interval: Seconds between keepalive events.
        abort_event: Optional event to signal abort.

    Yields:
        StreamEvent objects from the inner stream, interspersed with
        KEEPALIVE events during silence.
    """
    inner_iter = inner.__aiter__()
    pending_next: asyncio.Task | None = None

    try:
        while True:
            # Start fetching the next event if not already pending
            if pending_next is None:
                pending_next = asyncio.ensure_future(inner_iter.__anext__())

            # Build the wait set: next event + optional abort
            wait_set = {pending_next}
            abort_task: asyncio.Task | None = None
            if abort_event and not abort_event.is_set():
                abort_task = asyncio.ensure_future(abort_event.wait())
                wait_set.add(abort_task)

            # Wait for either the next event or timeout (keepalive)
            done, _ = await asyncio.wait(
                wait_set, timeout=interval, return_when=asyncio.FIRST_COMPLETED
            )

            # Clean up abort task if it wasn't triggered
            if abort_task and abort_task not in done:
                abort_task.cancel()
                try:
                    await abort_task
                except (asyncio.CancelledError, Exception):
                    pass

            # Abort was triggered
            if abort_task and abort_task in done:
                yield StreamEvent(event=StreamEventType.ABORTED, data={})
                return

            # Next event arrived
            if pending_next in done:
                try:
                    event = pending_next.result()
                    pending_next = None
                    yield event
                    # Stop on terminal events
                    if event.event in (
                        StreamEventType.DONE,
                        StreamEventType.ERROR,
                        StreamEventType.ABORTED,
                    ):
                        return
                except StopAsyncIteration:
                    return

            # Timeout — emit keepalive
            if not done:
                yield StreamEvent(event=StreamEventType.KEEPALIVE, data={})

    finally:
        # Cancel any pending task on cleanup
        if pending_next and not pending_next.done():
            pending_next.cancel()
            try:
                await pending_next
            except (asyncio.CancelledError, StopAsyncIteration, Exception):
                pass


# ── SSE formatter ────────────────────────────────────────────────────────────


def _format_sse(event: StreamEvent) -> dict:
    """Convert a StreamEvent to the SSE wire format for sse-starlette.

    Returns:
        Dict with "event" (str) and "data" (JSON string) keys.
    """
    return {
        "event": event.event.value,
        "data": json.dumps(event.data, default=str),
    }


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/{session_id}")
async def send_message(session_id: str, req: ChatRequest, request: Request):
    """Send a chat message and receive a streaming SSE response.

    Flow:
        1. Validate session exists
        2. Persist user message
        3. Build context window
        4. Stream LLM response with keepalive + abort
        5. Persist assistant message on completion
    """
    store = request.app.state.store
    llm = request.app.state.llm

    # Validate session
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Persist user message
    user_message = Message(role=Role.USER, content=req.content)
    await store.append_message(session_id, user_message)

    # Auto-title on first user message
    non_system = [m for m in session.messages if m.role != Role.SYSTEM]
    if len(non_system) == 0:
        session.title = req.content[:80].strip()
        await store.update(session)

    # Set up abort event
    abort_event = asyncio.Event()
    _abort_events[session_id] = abort_event

    async def event_generator():
        """Inner SSE generator — builds context, streams LLM, persists result."""
        content_buffer: list[str] = []
        tool_calls_buffer: dict[str, dict] = {}
        metadata_data: dict = {}
        final_status = MessageStatus.COMPLETE

        try:
            # Refresh session to include the appended user message
            session_fresh = await store.get(session_id)
            if session_fresh is None:
                yield _format_sse(StreamEvent(
                    event=StreamEventType.ERROR,
                    data={"error": "Session disappeared"},
                ))
                return

            # Build context window
            conv_messages = [m for m in session_fresh.messages if m.role != Role.SYSTEM]
            context_window, tokens_used = build_context_window(
                conv_messages,
                max_turns=req.max_context_turns,
            )

            # Create a STREAMING placeholder assistant message
            assistant_msg = Message(
                role=Role.ASSISTANT,
                content="",
                status=MessageStatus.STREAMING,
            )
            await store.append_message(session_id, assistant_msg)

            # Stream from LLM with keepalive
            stream = llm.stream_completion(context_window, abort_event=abort_event)
            wrapped = _keepalive_wrap(stream, abort_event=abort_event)

            async for event in wrapped:
                # Accumulate state for persistence
                if event.event == StreamEventType.TOKEN:
                    content_buffer.append(event.data.get("token", ""))
                elif event.event == StreamEventType.TOOL_CALL_START:
                    tc_id = event.data.get("id", "")
                    tool_calls_buffer[tc_id] = {
                        "id": tc_id,
                        "name": event.data.get("name", ""),
                        "arguments": {},
                        "result": None,
                    }
                elif event.event == StreamEventType.TOOL_CALL_END:
                    tc_id = event.data.get("id", "")
                    if tc_id in tool_calls_buffer:
                        tool_calls_buffer[tc_id]["arguments"] = event.data.get("arguments", {})
                elif event.event == StreamEventType.TOOL_RESULT:
                    tc_id = event.data.get("id", "")
                    if tc_id in tool_calls_buffer:
                        tool_calls_buffer[tc_id]["result"] = event.data.get("result", "")
                elif event.event == StreamEventType.METADATA:
                    metadata_data = event.data
                elif event.event == StreamEventType.ABORTED:
                    final_status = MessageStatus.ABORTED
                elif event.event == StreamEventType.ERROR:
                    final_status = MessageStatus.ERROR

                # Emit to client
                yield _format_sse(event)

                # Stop on terminal events
                if event.event in (
                    StreamEventType.DONE,
                    StreamEventType.ERROR,
                    StreamEventType.ABORTED,
                ):
                    break

        except asyncio.CancelledError:
            final_status = MessageStatus.ABORTED
            yield _format_sse(StreamEvent(event=StreamEventType.ABORTED, data={}))
        except Exception as e:
            final_status = MessageStatus.ERROR
            logger.error("chat.stream.error", extra={"error": str(e), "session_id": session_id})
            yield _format_sse(StreamEvent(
                event=StreamEventType.ERROR,
                data={"error": str(e)},
            ))
        finally:
            # Finalize and persist the assistant message
            try:
                from app.models import ToolCall as ToolCallModel
                assistant_msg.content = "".join(content_buffer)
                assistant_msg.status = final_status
                assistant_msg.tool_calls = [
                    ToolCallModel(
                        id=tc["id"],
                        name=tc["name"],
                        arguments=tc["arguments"],
                        result=tc["result"],
                    )
                    for tc in tool_calls_buffer.values()
                ]
                await store.update_message(session_id, assistant_msg)
            except Exception as e:
                logger.error("chat.finalize.error", extra={"error": str(e)})

            # Clean up abort event
            _abort_events.pop(session_id, None)

    return EventSourceResponse(event_generator(), ping=20)


@router.post("/{session_id}/abort")
async def abort_generation(session_id: str):
    """Cancel an in-flight chat generation for a session.

    Sets the abort event which is monitored by the keepalive wrapper.
    Idempotent — returns 204 even if no stream is active.
    """
    event = _abort_events.get(session_id)
    if event:
        event.set()
    return {"status": "ok"}
