"""Azure AI Foundry Agent LLM service — streams from a hosted Foundry agent.

Uses the azure-ai-projects SDK to invoke a Foundry agent and translate
its streaming responses into the SSE protocol.

Dependents:
    Created by llm.create_llm_service() when LLM_PROVIDER=agent.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator

from app.config import settings
from app.models import StreamEvent, StreamEventType, StreamMetadata

logger = logging.getLogger(__name__)


class AgentFrameworkService:
    """Azure AI Foundry Agent streaming provider.

    Connects to a hosted agent via the azure-ai-projects SDK, sends
    user messages, and translates AgentResponseUpdate events into
    the SSE StreamEvent protocol.
    """

    def __init__(self) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient

        # Validate required configuration
        if not settings.azure_ai_project_endpoint:
            raise ValueError(
                "AZURE_AI_PROJECT_ENDPOINT is required when LLM_PROVIDER=agent. "
                "Set it in your .env file."
            )
        if not settings.azure_ai_agent_id:
            raise ValueError(
                "AZURE_AI_AGENT_ID is required when LLM_PROVIDER=agent. "
                "Set it in your .env file."
            )

        # Create the AI Project client with DefaultAzureCredential
        self._credential = DefaultAzureCredential()
        self._client = AIProjectClient(
            endpoint=settings.azure_ai_project_endpoint,
            credential=self._credential,
        )
        self._agent_id = settings.azure_ai_agent_id

        logger.info(
            "llm.agent.initialized",
            extra={
                "endpoint": settings.azure_ai_project_endpoint,
                "agent_id": self._agent_id,
            },
        )

    async def stream_completion(
        self,
        messages: list[dict],
        *,
        abort_event: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion from the Foundry agent.

        Extracts the user message from the context window, sends it to
        the agent, and translates the response updates into StreamEvents.
        """
        start = time.monotonic()
        prompt_tokens = 0
        completion_tokens = 0

        # Extract last user message from context window
        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break

        if not user_msg:
            yield StreamEvent(
                event=StreamEventType.ERROR,
                data={"error": "No user message found in context"},
            )
            return

        try:
            # Create a thread and run the agent
            agents_client = self._client.agents
            thread = agents_client.threads.create()
            agents_client.messages.create(
                thread_id=thread.id,
                role="user",
                content=user_msg,
            )

            # Stream the agent run
            with agents_client.runs.stream(
                thread_id=thread.id,
                agent_id=self._agent_id,
            ) as stream:
                for event_type, event_data, _raw in stream:
                    # Check abort
                    if abort_event and abort_event.is_set():
                        yield StreamEvent(event=StreamEventType.ABORTED, data={})
                        return

                    # Map agent events to SSE events
                    if event_type == "thread.message.delta":
                        # Text content delta
                        if hasattr(event_data, "delta") and event_data.delta.content:
                            for content_part in event_data.delta.content:
                                if hasattr(content_part, "text") and content_part.text:
                                    text_value = content_part.text.value if hasattr(content_part.text, "value") else str(content_part.text)
                                    yield StreamEvent(
                                        event=StreamEventType.TOKEN,
                                        data={"token": text_value},
                                    )

                    elif event_type == "thread.run.step.delta":
                        # Tool call deltas
                        if hasattr(event_data, "delta") and hasattr(event_data.delta, "step_details"):
                            step = event_data.delta.step_details
                            if hasattr(step, "tool_calls"):
                                for tc in step.tool_calls:
                                    if hasattr(tc, "function"):
                                        tc_id = tc.id or f"call_{uuid.uuid4().hex[:12]}"
                                        if tc.function.name:
                                            yield StreamEvent(
                                                event=StreamEventType.TOOL_CALL_START,
                                                data={"id": tc_id, "name": tc.function.name},
                                            )

                    elif event_type == "thread.run.completed":
                        # Extract usage from the completed run
                        if hasattr(event_data, "usage") and event_data.usage:
                            prompt_tokens = getattr(event_data.usage, "prompt_tokens", 0)
                            completion_tokens = getattr(event_data.usage, "completion_tokens", 0)

            # Clean up the thread
            agents_client.threads.delete(thread.id)

        except Exception as e:
            logger.error("llm.agent.stream_error", extra={"error": str(e)})
            yield StreamEvent(
                event=StreamEventType.ERROR,
                data={"error": f"Agent error: {str(e)}"},
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
                model=f"agent:{self._agent_id}",
                assistant_message_id=uuid.uuid4().hex,
            ).model_dump(),
        )
        yield StreamEvent(event=StreamEventType.DONE, data={})
