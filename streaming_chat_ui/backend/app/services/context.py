"""Context assembly — token counting and sliding-window trimming.

Builds a token-budgeted context window from conversation history,
ensuring the system prompt is always included and messages are filled
newest-first until the token budget is exhausted.

Dependents:
    Called by the chat router to build the context for LLM calls.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.models import Message, Role

logger = logging.getLogger(__name__)

# ── Tokenizer initialization ────────────────────────────────────────────────
# tiktoken for accurate token counting; falls back to cl100k_base if the
# configured model isn't in tiktoken's registry.
import tiktoken

try:
    _encoder = tiktoken.encoding_for_model(settings.llm_model)
except KeyError:
    _encoder = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count the number of tokens in a text string."""
    return len(_encoder.encode(text))


def _message_tokens(msg: Message) -> int:
    """Estimate token count for a single message (content + tool calls + overhead)."""
    overhead = 4  # per-message framing tokens
    content_tokens = count_tokens(msg.content) if msg.content else 0
    tool_tokens = sum(
        count_tokens(tc.name) + count_tokens(str(tc.arguments))
        for tc in msg.tool_calls
    )
    return overhead + content_tokens + tool_tokens


def build_context_window(
    messages: list[Message],
    system_prompt: str | None = None,
    max_turns: int | None = None,
) -> tuple[list[dict], int]:
    """Build a token-budgeted context window from conversation messages.

    Args:
        messages: Full conversation history (excluding system prompt).
        system_prompt: System prompt text. Falls back to settings.system_prompt.
        max_turns: Optional limit on the number of turn pairs (N turns = N*2 messages).

    Returns:
        Tuple of (context_window_dicts, tokens_used). The context window
        always starts with the system message.
    """
    prompt = system_prompt or settings.system_prompt
    budget = settings.max_context_tokens - settings.max_response_tokens
    total_budget = budget

    # Pre-slice by max_turns if specified (N turn pairs = N*2 messages)
    if max_turns is not None and max_turns > 0:
        slice_count = max_turns * 2
        if len(messages) > slice_count:
            messages = messages[-slice_count:]

    # System prompt always first
    system_msg = {"role": "system", "content": prompt}
    budget -= count_tokens(prompt) + 4

    if budget <= 0:
        logger.warning("Context budget exhausted by system prompt alone")
        return [system_msg], total_budget - budget

    # Fill from newest → oldest until budget exhausted
    formatted: list[dict] = []
    for msg in reversed(messages):
        msg_dict: dict = {"role": msg.role.value, "content": msg.content}
        if msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": str(tc.arguments)},
                }
                for tc in msg.tool_calls
            ]
        tokens = _message_tokens(msg)
        if tokens > budget:
            break
        budget -= tokens
        formatted.append(msg_dict)

    # Reverse back to chronological order
    formatted.reverse()
    tokens_used = total_budget - budget

    logger.info(
        "context.built",
        extra={
            "messages_total": len(messages),
            "messages_kept": len(formatted),
            "messages_dropped": len(messages) - len(formatted),
            "tokens_used": tokens_used,
            "tokens_budget": total_budget,
        },
    )

    return [system_msg, *formatted], tokens_used
