"""OpenAI-compatible LLM service — streams from OpenAI / Azure OpenAI endpoints.

Uses the OpenAI Python SDK for streaming chat completions with tool call support.

Dependents:
    Created by llm.create_llm_service() when LLM_PROVIDER=openai.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator

from openai import AsyncAzureOpenAI, AsyncOpenAI

from app.config import settings
from app.models import StreamEvent, StreamEventType, StreamMetadata

logger = logging.getLogger(__name__)


class OpenAILLMService:
    """OpenAI / Azure OpenAI streaming provider.

    Auto-detects Azure vs. standard OpenAI based on whether llm_base_url
    contains 'openai.azure.com'.
    """

    def __init__(self) -> None:
        # Determine client type from the base URL
        if "openai.azure.com" in settings.llm_base_url:
            self._client = AsyncAzureOpenAI(
                azure_endpoint=settings.llm_base_url,
                api_key=settings.llm_api_key,
                api_version="2024-12-01-preview",
            )
            logger.info("llm.openai.azure_client_created", extra={"endpoint": settings.llm_base_url})
        else:
            self._client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url or None,
            )
            logger.info("llm.openai.client_created")

    async def stream_completion(
        self,
        messages: list[dict],
        *,
        abort_event: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion from OpenAI.

        Handles text tokens and tool call deltas, emitting the full
        streaming protocol (TOKEN, TOOL_CALL_START/END/RESULT, METADATA, DONE).
        """
        start = time.monotonic()
        prompt_tokens = 0
        completion_tokens = 0
        model_name = settings.llm_model

        try:
            stream = await self._client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
            )

            # Track tool call state across deltas
            tool_calls: dict[int, dict] = {}  # index → {id, name, arguments_buffer}

            async for chunk in stream:
                # Check for abort
                if abort_event and abort_event.is_set():
                    yield StreamEvent(event=StreamEventType.ABORTED, data={})
                    return

                # Extract usage if present (final chunk)
                if chunk.usage:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Text token
                if delta.content:
                    yield StreamEvent(
                        event=StreamEventType.TOKEN,
                        data={"token": delta.content},
                    )

                # Tool call deltas
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls:
                            # New tool call — emit start
                            tc_id = tc_delta.id or f"call_{uuid.uuid4().hex[:12]}"
                            tc_name = tc_delta.function.name or ""
                            tool_calls[idx] = {
                                "id": tc_id,
                                "name": tc_name,
                                "arguments_buffer": "",
                            }
                            yield StreamEvent(
                                event=StreamEventType.TOOL_CALL_START,
                                data={"id": tc_id, "name": tc_name},
                            )

                        # Accumulate argument deltas
                        if tc_delta.function and tc_delta.function.arguments:
                            tool_calls[idx]["arguments_buffer"] += tc_delta.function.arguments

                # Finish reason — emit tool call ends
                if chunk.choices[0].finish_reason == "tool_calls":
                    import json
                    for tc_data in tool_calls.values():
                        try:
                            args = json.loads(tc_data["arguments_buffer"])
                        except (json.JSONDecodeError, KeyError):
                            args = {"raw": tc_data["arguments_buffer"]}
                        yield StreamEvent(
                            event=StreamEventType.TOOL_CALL_END,
                            data={
                                "id": tc_data["id"],
                                "name": tc_data["name"],
                                "arguments": args,
                            },
                        )

        except Exception as e:
            logger.error("llm.openai.stream_error", extra={"error": str(e)})
            yield StreamEvent(
                event=StreamEventType.ERROR,
                data={"error": str(e)},
            )
            return

        # Metadata + done
        duration_ms = (time.monotonic() - start) * 1000
        yield StreamEvent(
            event=StreamEventType.METADATA,
            data=StreamMetadata(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                duration_ms=round(duration_ms, 1),
                model=model_name,
                assistant_message_id=uuid.uuid4().hex,
            ).model_dump(),
        )
        yield StreamEvent(event=StreamEventType.DONE, data={})
