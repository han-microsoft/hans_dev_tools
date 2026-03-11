#!/usr/bin/env python3
"""
benchmark.py — Compare PathfinderIQ-style vs Native Context Provider

Runs 10 multi-turn conversations through both methods, measuring:
  - Time to first token (TTFT)
  - Total response time per turn
  - Total tokens (estimated from response length)
  - Context injection overhead

USAGE:
  export AZURE_AI_PROJECT_ENDPOINT="https://..."
  export AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME="gpt-5-mini"
  cd /home/hanchoong/han_tools/foundry_agent_context_demo
  python3 benchmark.py

  # Custom settings:
  MAX_CONTEXT_MESSAGES=5 python3 benchmark.py
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field


# ── Persistent event loop on a background thread ─────────────────────────────
# All async SDK calls run on this loop. Solves the aiohttp "Future attached to
# a different loop" error that occurs when creating new loops per call.

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_thread: threading.Thread | None = None


def _ensure_bg_loop() -> asyncio.AbstractEventLoop:
    global _bg_loop, _bg_thread
    if _bg_loop is not None and _bg_loop.is_running():
        return _bg_loop
    _bg_loop = asyncio.new_event_loop()
    _bg_thread = threading.Thread(target=_bg_loop.run_forever, daemon=True)
    _bg_thread.start()
    return _bg_loop


def _run_async(coro):
    """Submit a coroutine to the background loop and wait for result."""
    loop = _ensure_bg_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=300)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

PROJECT_ENDPOINT = os.environ.get(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://pulsenextfoundry.services.ai.azure.com/api/projects/proj-default",
)
MODEL = os.environ.get("AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME", "gpt-5-mini")
MAX_CTX = int(os.environ.get("MAX_CONTEXT_MESSAGES", "10"))

# ═══════════════════════════════════════════════════════════════════════════════
# TEST CONVERSATIONS — 10 multi-turn scenarios
# ═══════════════════════════════════════════════════════════════════════════════

CONVERSATIONS: list[list[str]] = [
    # 1. Name recall (3 turns)
    ["My name is Alice.", "What color is the sky?", "What is my name?"],
    # 2. Math chain (3 turns)
    ["What is 7 * 8?", "Add 12 to that.", "Divide the result by 2."],
    # 3. Story building (4 turns)
    ["Start a story about a cat named Whiskers.", "The cat finds a treasure map.",
     "Whiskers follows the map to a cave.", "How does the story end?"],
    # 4. Technical Q&A (3 turns)
    ["What is a circuit breaker pattern?", "Give me a Python example.",
     "How does it differ from a retry pattern?"],
    # 5. Translation chain (3 turns)
    ["How do you say hello in Japanese?", "And goodbye?", "Teach me to count to 3."],
    # 6. Context switch test (4 turns)
    ["I live in Sydney.", "What's the weather like there typically?",
     "Actually I moved to Tokyo last year.", "What's the weather like where I live now?"],
    # 7. Instruction following (3 turns)
    ["From now on, end every response with '— Chef Bot'.",
     "What's a good recipe for pasta?", "How about a salad?"],
    # 8. Summarization (3 turns)
    ["The quick brown fox jumps over the lazy dog. The dog didn't mind because it was sleeping.",
     "Summarize what you just read in one sentence.",
     "Now make it even shorter — 5 words max."],
    # 9. Persona recall (3 turns)
    ["I'm a software engineer who loves Python.", "What book should I read next?",
     "Remind me — what's my profession?"],
    # 10. Long context (4 turns)
    ["Remember these numbers: 42, 17, 93, 61, 28.",
     "What was the third number I told you?",
     "Now add the first and last numbers.",
     "List all five numbers in reverse order."],
]


# ═══════════════════════════════════════════════════════════════════════════════
# MEASUREMENT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TurnResult:
    turn: int
    user_message: str
    response: str
    ttft_ms: float          # Time to first token
    total_ms: float         # Total response time
    response_chars: int     # Response length in chars
    est_tokens: int         # Estimated tokens (~4 chars per token)


@dataclass
class ConversationResult:
    conv_id: int
    method: str
    turns: list[TurnResult] = field(default_factory=list)

    @property
    def total_time_ms(self) -> float:
        return sum(t.total_ms for t in self.turns)

    @property
    def total_est_tokens(self) -> int:
        return sum(t.est_tokens for t in self.turns)

    @property
    def avg_ttft_ms(self) -> float:
        ttfts = [t.ttft_ms for t in self.turns if t.ttft_ms > 0]
        return sum(ttfts) / len(ttfts) if ttfts else 0


# ═══════════════════════════════════════════════════════════════════════════════
# METHOD A: PathfinderIQ Style (message injection)
# ═══════════════════════════════════════════════════════════════════════════════

_piq_client = None
_piq_agent = None

def _piq_setup():
    global _piq_client, _piq_agent
    if _piq_agent is not None:
        return

    async def _build():
        global _piq_client, _piq_agent
        from agent_framework.azure import AzureAIAgentClient
        from azure.identity import DefaultAzureCredential
        _piq_client = AzureAIAgentClient(
            project_endpoint=PROJECT_ENDPOINT,
            model_deployment_name=MODEL,
            credential=DefaultAzureCredential(),
        )
        _piq_agent = _piq_client.as_agent(
            name="BenchPIQ",
            instructions="You are a helpful assistant. Answer concisely. "
                         "Use <prior_conversation> blocks for context. "
                         "Do not mention the block.",
            tools=None,
            default_options={"model_id": MODEL},
        )

    _run_async(_build())
    print("  [PIQ] Agent built")


def _piq_build_context(history: list[dict], user_msg: str) -> str:
    prior = history[-(MAX_CTX + 1):-1] if len(history) > 1 else []
    if not prior:
        return user_msg
    lines = []
    for m in prior:
        role = m["role"].upper()
        content = m["content"][:2000]
        lines.append(f"{role}: {content}")
    block = "\n".join(lines)
    return f"<prior_conversation>\n{block}\n</prior_conversation>\n\n{user_msg}"


def run_piq_conversation(conv_id: int, messages: list[str]) -> ConversationResult:
    from agent_framework import AgentSession, AgentResponseUpdate

    _piq_setup()
    result = ConversationResult(conv_id=conv_id, method="pathfinderiq")
    history: list[dict] = []

    for turn_idx, user_msg in enumerate(messages):
        history.append({"role": "user", "content": user_msg})
        augmented = _piq_build_context(history, user_msg)

        # Fresh session per request (PathfinderIQ pattern)
        session = AgentSession()
        chunks: list[str] = []
        ttft = 0.0
        start = time.monotonic()

        async def _stream():
            nonlocal ttft
            async for update in _piq_agent.run(augmented, stream=True, session=session):
                if not isinstance(update, AgentResponseUpdate):
                    continue
                for c in (update.contents or []):
                    if hasattr(c, "text") and c.text:
                        if not chunks:
                            ttft = (time.monotonic() - start) * 1000
                        chunks.append(c.text)

        _run_async(_stream())

        elapsed = (time.monotonic() - start) * 1000
        response = "".join(chunks)
        history.append({"role": "assistant", "content": response})

        result.turns.append(TurnResult(
            turn=turn_idx + 1,
            user_message=user_msg,
            response=response[:100],
            ttft_ms=round(ttft, 1),
            total_ms=round(elapsed, 1),
            response_chars=len(response),
            est_tokens=max(1, len(response) // 4),
        ))

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# METHOD B: Native Context Provider (WindowedHistoryProvider)
# ═══════════════════════════════════════════════════════════════════════════════

_nat_client = None
_nat_agent = None

def _nat_setup():
    global _nat_client, _nat_agent
    if _nat_agent is not None:
        return

    async def _build():
        global _nat_client, _nat_agent
        from agent_framework.azure import AzureAIAgentClient
        from agent_framework._sessions import BaseHistoryProvider, Message
        from azure.identity import DefaultAzureCredential
        from typing import Any, Sequence

        class WindowedHistoryProvider(BaseHistoryProvider):
            def __init__(self, max_messages: int):
                super().__init__(
                    source_id="windowed_history",
                    load_messages=True, store_inputs=True, store_outputs=True,
                )
                self.max_messages = max_messages

            async def get_messages(self, session_id, *, state=None, **kw):
                if state is None:
                    return []
                return list(state.get("messages", []))[-self.max_messages:]

            async def save_messages(self, session_id, messages, *, state=None, **kw):
                if state is None:
                    return
                existing = state.get("messages", [])
                state["messages"] = [*existing, *messages]

        _nat_client = AzureAIAgentClient(
            project_endpoint=PROJECT_ENDPOINT,
            model_deployment_name=MODEL,
            credential=DefaultAzureCredential(),
        )
        _nat_agent = _nat_client.as_agent(
            name="BenchNative",
            instructions="You are a helpful assistant. Answer concisely.",
            tools=None,
            default_options={"model_id": MODEL},
            context_providers=[WindowedHistoryProvider(max_messages=MAX_CTX)],
        )

    _run_async(_build())
    print("  [NAT] Agent built")


def run_native_conversation(conv_id: int, messages: list[str]) -> ConversationResult:
    from agent_framework import AgentSession, AgentResponseUpdate

    _nat_setup()
    result = ConversationResult(conv_id=conv_id, method="native")

    # Persistent session (native pattern — provider manages history)
    session = AgentSession()

    for turn_idx, user_msg in enumerate(messages):
        chunks: list[str] = []
        ttft = 0.0
        start = time.monotonic()

        async def _stream():
            nonlocal ttft
            async for update in _nat_agent.run(user_msg, stream=True, session=session):
                if not isinstance(update, AgentResponseUpdate):
                    continue
                for c in (update.contents or []):
                    if hasattr(c, "text") and c.text:
                        if not chunks:
                            ttft = (time.monotonic() - start) * 1000
                        chunks.append(c.text)

        _run_async(_stream())

        elapsed = (time.monotonic() - start) * 1000
        response = "".join(chunks)

        result.turns.append(TurnResult(
            turn=turn_idx + 1,
            user_message=user_msg,
            response=response[:100],
            ttft_ms=round(ttft, 1),
            total_ms=round(elapsed, 1),
            response_chars=len(response),
            est_tokens=max(1, len(response) // 4),
        ))

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def print_report(piq_results: list[ConversationResult], nat_results: list[ConversationResult]):
    SEP = "─" * 90

    print(f"\n{'═' * 90}")
    print(f"  BENCHMARK REPORT — PathfinderIQ vs Native Context Provider")
    print(f"  Model: {MODEL} | Context window: {MAX_CTX} messages")
    print(f"  Conversations: {len(CONVERSATIONS)} | Total turns: {sum(len(c) for c in CONVERSATIONS)}")
    print(f"{'═' * 90}\n")

    # Per-conversation comparison
    print(f"{'Conv':>4}  {'Turns':>5}  {'Method':<14}  {'Total ms':>9}  {'Avg TTFT':>9}  {'Est Tokens':>10}  {'Context':>10}")
    print(SEP)

    for i in range(len(piq_results)):
        p, n = piq_results[i], nat_results[i]
        turns = len(CONVERSATIONS[i])
        print(f"{i+1:>4}  {turns:>5}  {'PathfinderIQ':<14}  {p.total_time_ms:>9.0f}  {p.avg_ttft_ms:>9.0f}  {p.total_est_tokens:>10}  {'injection':>10}")
        print(f"{'':>4}  {'':>5}  {'Native':<14}  {n.total_time_ms:>9.0f}  {n.avg_ttft_ms:>9.0f}  {n.total_est_tokens:>10}  {'provider':>10}")
        # Delta
        time_delta = n.total_time_ms - p.total_time_ms
        token_delta = n.total_est_tokens - p.total_est_tokens
        print(f"{'':>4}  {'':>5}  {'Δ (nat-piq)':<14}  {time_delta:>+9.0f}  {'':>9}  {token_delta:>+10}")
        print(SEP)

    # Aggregates
    piq_total_time = sum(r.total_time_ms for r in piq_results)
    nat_total_time = sum(r.total_time_ms for r in nat_results)
    piq_total_tokens = sum(r.total_est_tokens for r in piq_results)
    nat_total_tokens = sum(r.total_est_tokens for r in nat_results)
    piq_avg_ttft = sum(r.avg_ttft_ms for r in piq_results) / len(piq_results)
    nat_avg_ttft = sum(r.avg_ttft_ms for r in nat_results) / len(nat_results)

    print(f"\n{'═' * 90}")
    print(f"  AGGREGATE SUMMARY")
    print(f"{'═' * 90}")
    print(f"  {'Metric':<30}  {'PathfinderIQ':>15}  {'Native':>15}  {'Δ':>10}")
    print(f"  {SEP[:80]}")
    print(f"  {'Total time (ms)':<30}  {piq_total_time:>15.0f}  {nat_total_time:>15.0f}  {nat_total_time - piq_total_time:>+10.0f}")
    print(f"  {'Avg TTFT (ms)':<30}  {piq_avg_ttft:>15.0f}  {nat_avg_ttft:>15.0f}  {nat_avg_ttft - piq_avg_ttft:>+10.0f}")
    print(f"  {'Est total tokens':<30}  {piq_total_tokens:>15}  {nat_total_tokens:>15}  {nat_total_tokens - piq_total_tokens:>+10}")
    print(f"  {'Avg time/turn (ms)':<30}  {piq_total_time / sum(len(c) for c in CONVERSATIONS):>15.0f}  {nat_total_time / sum(len(c) for c in CONVERSATIONS):>15.0f}")

    # Context accuracy — check if name/number recall worked
    print(f"\n{'═' * 90}")
    print(f"  CONTEXT ACCURACY (spot checks)")
    print(f"{'═' * 90}")

    checks = [
        (0, 2, "alice", "Name recall: 'Alice' in response"),
        (5, 3, "tokyo", "Context switch: 'Tokyo' in response"),
        (8, 2, "software", "Persona recall: 'software' in response"),
        (9, 1, "93", "Number recall: '93' (3rd number) in response"),
    ]

    for conv_idx, turn_idx, keyword, desc in checks:
        if conv_idx < len(piq_results) and turn_idx < len(piq_results[conv_idx].turns):
            piq_pass = keyword.lower() in piq_results[conv_idx].turns[turn_idx].response.lower()
            nat_pass = keyword.lower() in nat_results[conv_idx].turns[turn_idx].response.lower()
            print(f"  {desc:<45}  PIQ: {'✓' if piq_pass else '✗'}  NAT: {'✓' if nat_pass else '✗'}")

    print(f"\n{'═' * 90}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'═' * 60}")
    print(f"  Benchmark: PathfinderIQ vs Native Context Provider")
    print(f"  Model: {MODEL}")
    print(f"  Context window: {MAX_CTX} messages")
    print(f"  Conversations: {len(CONVERSATIONS)}")
    print(f"  Total turns: {sum(len(c) for c in CONVERSATIONS)}")
    print(f"  Endpoint: {PROJECT_ENDPOINT[:50]}...")
    print(f"{'═' * 60}\n")

    # Run PathfinderIQ style
    print("▶ Running PathfinderIQ style (message injection)...")
    piq_results: list[ConversationResult] = []
    for i, conv in enumerate(CONVERSATIONS):
        print(f"  Conv {i+1}/{len(CONVERSATIONS)} ({len(conv)} turns)...", end=" ", flush=True)
        t0 = time.monotonic()
        result = run_piq_conversation(i, conv)
        elapsed = time.monotonic() - t0
        piq_results.append(result)
        print(f"done ({elapsed:.1f}s)")

    print()

    # Run Native context provider
    print("▶ Running Native context provider (WindowedHistoryProvider)...")
    nat_results: list[ConversationResult] = []
    for i, conv in enumerate(CONVERSATIONS):
        print(f"  Conv {i+1}/{len(CONVERSATIONS)} ({len(conv)} turns)...", end=" ", flush=True)
        t0 = time.monotonic()
        result = run_native_conversation(i, conv)
        elapsed = time.monotonic() - t0
        nat_results.append(result)
        print(f"done ({elapsed:.1f}s)")

    # Report
    print_report(piq_results, nat_results)


if __name__ == "__main__":
    main()
