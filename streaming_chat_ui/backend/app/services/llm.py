"""LLM service — protocol, factory, and dev providers.

Defines the LLMService protocol that all providers implement, plus
a factory function that selects the appropriate provider based on
the LLM_PROVIDER setting.

Dev providers (echo, mock) require zero external dependencies and
are defined inline for convenience.

Dependents:
    - main.py creates the LLM service at startup via create_llm_service()
    - chat router streams from LLMService.stream_completion()
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from app.config import settings
from app.models import StreamEvent, StreamEventType, StreamMetadata

logger = logging.getLogger(__name__)


# ── Protocol ─────────────────────────────────────────────────────────────────


@runtime_checkable
class LLMService(Protocol):
    """Interface for LLM completion providers."""

    async def stream_completion(
        self,
        messages: list[dict],
        *,
        abort_event: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream completion events from the LLM.

        Args:
            messages: The context window (system + conversation history).
            abort_event: Optional event to signal abort.

        Yields:
            StreamEvent objects (TOKEN, TOOL_CALL_*, METADATA, DONE, ERROR).
        """
        ...


# ── Factory ──────────────────────────────────────────────────────────────────


def create_llm_service() -> LLMService:
    """Create an LLM service based on the LLM_PROVIDER setting.

    Returns:
        An LLMService implementation:
        - "echo" → EchoLLMService (parrots user message word-by-word)
        - "mock" → MockLLMService (canned response with tool calls)
        - "agent" → AgentFrameworkService (Azure AI Foundry)
        - default → OpenAILLMService (OpenAI-compatible API)
    """
    provider = settings.llm_provider.lower()
    logger.info("llm.provider.selected", extra={"provider": provider})

    if provider == "echo":
        return EchoLLMService()
    elif provider == "mock":
        return MockLLMService()
    elif provider == "agent":
        from app.services.llm_agent import AgentFrameworkService
        return AgentFrameworkService()
    else:
        from app.services.llm_openai import OpenAILLMService
        return OpenAILLMService()


# ── Echo Provider (dev) ──────────────────────────────────────────────────────


class EchoLLMService:
    """Dev provider that parrots the user's message back word-by-word.

    Zero external dependencies. Useful for testing the streaming pipeline
    end-to-end without an LLM endpoint.
    """

    async def stream_completion(
        self,
        messages: list[dict],
        *,
        abort_event: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Echo the last user message back as streamed tokens."""
        start = time.monotonic()

        # Extract last user message from the context window
        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        if not user_msg:
            user_msg = "(no user message found)"

        # Prefix the echo with an indicator
        echo_text = f"**Echo:** {user_msg}"
        words = echo_text.split(" ")

        # Stream word-by-word with 50ms delays
        for i, word in enumerate(words):
            if abort_event and abort_event.is_set():
                yield StreamEvent(event=StreamEventType.ABORTED, data={})
                return
            token = word if i == 0 else f" {word}"
            yield StreamEvent(event=StreamEventType.TOKEN, data={"token": token})
            await asyncio.sleep(0.05)

        # Emit metadata
        duration_ms = (time.monotonic() - start) * 1000
        msg_id = uuid.uuid4().hex
        yield StreamEvent(
            event=StreamEventType.METADATA,
            data=StreamMetadata(
                prompt_tokens=len(user_msg.split()),
                completion_tokens=len(words),
                total_tokens=len(user_msg.split()) + len(words),
                duration_ms=round(duration_ms, 1),
                model="echo",
                assistant_message_id=msg_id,
            ).model_dump(),
        )
        yield StreamEvent(event=StreamEventType.DONE, data={})


# ── Mock Provider (dev) ──────────────────────────────────────────────────────


def _tokenize_mock(text: str) -> list[str]:
    """Split text into tokens preserving whitespace/newlines for realistic streaming."""
    tokens: list[str] = []
    current = ""
    for ch in text:
        if ch in (" ", "\n"):
            if current:
                tokens.append(current)
            tokens.append(ch)
            current = ""
        else:
            current += ch
    if current:
        tokens.append(current)
    return tokens


class MockLLMService:
    """Dev provider with a canned rich response including tool calls.

    Exercises the full streaming protocol: thinking, tool calls with
    results, and markdown text output. No external dependencies.
    """

    async def stream_completion(
        self,
        messages: list[dict],
        *,
        abort_event: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a canned response with tool calls and markdown."""
        start = time.monotonic()

        # 1. Thinking step
        yield StreamEvent(
            event=StreamEventType.THINKING,
            data={"title": "Analyzing request", "detail": "Reviewing the user's query and determining the best approach."},
        )
        await asyncio.sleep(0.3)

        # 2. Tool call: search_documentation
        tc_id_1 = f"call_{uuid.uuid4().hex[:12]}"
        yield StreamEvent(
            event=StreamEventType.TOOL_CALL_START,
            data={"id": tc_id_1, "name": "search_documentation"},
        )
        await asyncio.sleep(0.5)
        yield StreamEvent(
            event=StreamEventType.TOOL_CALL_END,
            data={"id": tc_id_1, "name": "search_documentation", "arguments": {"query": "streaming chat UI architecture"}},
        )
        await asyncio.sleep(0.3)
        yield StreamEvent(
            event=StreamEventType.TOOL_RESULT,
            data={"id": tc_id_1, "name": "search_documentation", "result": '{"results": [{"title": "SSE Streaming Guide", "snippet": "Server-Sent Events provide a one-way channel..."}], "count": 1}'},
        )
        await asyncio.sleep(0.2)

        # 3. Tool call: read_file
        tc_id_2 = f"call_{uuid.uuid4().hex[:12]}"
        yield StreamEvent(
            event=StreamEventType.TOOL_CALL_START,
            data={"id": tc_id_2, "name": "read_file"},
        )
        await asyncio.sleep(0.4)
        yield StreamEvent(
            event=StreamEventType.TOOL_CALL_END,
            data={"id": tc_id_2, "name": "read_file", "arguments": {"path": "src/api/chatApi.ts", "lines": "1-50"}},
        )
        await asyncio.sleep(0.2)
        yield StreamEvent(
            event=StreamEventType.TOOL_RESULT,
            data={"id": tc_id_2, "name": "read_file", "result": '{"columns": ["line", "content"], "data": [["1", "import { BASE } from \\"./client\\";"], ["2", "export async function streamChat() { ... }"]]}'},
        )
        await asyncio.sleep(0.2)

        # 4. Streamed text response with markdown
        response = (
            "## Analysis Complete\n\n"
            "Based on the documentation and code review, here's a summary:\n\n"
            "### Key Findings\n\n"
            "1. **SSE Protocol**: The streaming uses `fetch + ReadableStream` (not EventSource) "
            "for POST support and AbortController integration.\n"
            "2. **Content Parts**: Messages use an interleaved `ContentPart[]` model supporting "
            "text, thinking, and tool_call parts.\n"
            "3. **Keepalive**: A 15-second heartbeat prevents proxy/browser timeout.\n\n"
            "### Architecture\n\n"
            "| Layer | Technology | Purpose |\n"
            "|-------|-----------|----------|\n"
            "| Backend | FastAPI + SSE | Event streaming |\n"
            "| State | Zustand | Per-agent slices |\n"
            "| Rendering | React + Tailwind | Component UI |\n\n"
            "```python\n"
            "async def stream_completion(messages):\n"
            '    yield StreamEvent(event="token", data={"token": "Hello"})\n'
            "```\n\n"
            "The implementation is production-ready with abort support, "
            "idle timeouts, and graceful error handling."
        )

        tokens = _tokenize_mock(response)
        for token in tokens:
            if abort_event and abort_event.is_set():
                yield StreamEvent(event=StreamEventType.ABORTED, data={})
                return
            yield StreamEvent(event=StreamEventType.TOKEN, data={"token": token})
            await asyncio.sleep(0.02)

        # 5. Metadata + done
        duration_ms = (time.monotonic() - start) * 1000
        yield StreamEvent(
            event=StreamEventType.METADATA,
            data=StreamMetadata(
                prompt_tokens=150,
                completion_tokens=len(tokens),
                total_tokens=150 + len(tokens),
                duration_ms=round(duration_ms, 1),
                model="mock",
                assistant_message_id=uuid.uuid4().hex,
                estimated_cost_usd=0.003,
            ).model_dump(),
        )
        yield StreamEvent(event=StreamEventType.DONE, data={})
