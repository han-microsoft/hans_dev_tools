#!/usr/bin/env python3
"""
native_context_demo.py — Foundry Agent with Native Context Provider

Uses the agent_framework's BaseHistoryProvider to control context natively,
instead of the PathfinderIQ-style message injection hack.

THE NATIVE PATTERN:
  1. Subclass BaseHistoryProvider with a windowed get_messages()
  2. Register it on the agent via context_providers=[...]
  3. REUSE the same AgentSession across requests (it accumulates state)
  4. The provider returns only the last N messages to the LLM each turn
  5. Full history stays in session.state — the window slides automatically

COMPARISON WITH PATHFINDERIQ PATTERN:
  PathfinderIQ: Fresh AgentSession per request + inject <prior_conversation> block
  Native:       Persistent AgentSession + WindowedHistoryProvider slices context

USAGE:
  export AZURE_AI_PROJECT_ENDPOINT="https://<foundry>.services.ai.azure.com/api/projects/<project>"
  export AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME="gpt-4.1"
  MAX_CONTEXT_MESSAGES=10 python3 native_context_demo.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any, Sequence

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("native_context_demo")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

PROJECT_ENDPOINT = os.environ.get(
    "AZURE_AI_PROJECT_ENDPOINT",
    "https://pulsenextfoundry.services.ai.azure.com/api/projects/proj-default",
)
MODEL_DEPLOYMENT = os.environ.get("AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME", "gpt-5-mini")
MAX_CONTEXT_MESSAGES = int(os.environ.get("MAX_CONTEXT_MESSAGES", "10"))
PORT = int(os.environ.get("PORT", "7860"))

if not PROJECT_ENDPOINT:
    print("ERROR: Set AZURE_AI_PROJECT_ENDPOINT environment variable.")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# CORE: Windowed History Provider (the native answer to the colleague's question)
# ═══════════════════════════════════════════════════════════════════════════════

from agent_framework._sessions import BaseHistoryProvider, Message


class WindowedHistoryProvider(BaseHistoryProvider):
    """History provider that returns only the last N messages to the LLM.

    Stores ALL messages in session.state (for persistence), but only loads
    the most recent `max_messages` into context before each invocation.
    This gives you full control over context size without hacks.

    The full history remains available in session.state["messages"] for
    audit, export, or UI display.
    """

    def __init__(self, max_messages: int = 10):
        super().__init__(
            source_id="windowed_history",
            load_messages=True,    # Load history before invocation
            store_inputs=True,     # Save user messages after invocation
            store_outputs=True,    # Save assistant responses after invocation
        )
        self.max_messages = max_messages

    async def get_messages(
        self, session_id: str | None, *, state: dict[str, Any] | None = None, **kwargs: Any
    ) -> list[Message]:
        """Return only the last N messages from stored history.

        This is where the context windowing happens. The full history is
        in state["messages"], but we only return a slice to the agent.
        """
        if state is None:
            return []
        all_msgs: list[Message] = state.get("messages", [])
        windowed = all_msgs[-self.max_messages:]
        logger.info(
            "WindowedHistoryProvider: %d/%d messages loaded (window=%d)",
            len(windowed), len(all_msgs), self.max_messages,
        )
        return windowed

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Append messages to the full history in state."""
        if state is None:
            return
        existing = state.get("messages", [])
        state["messages"] = [*existing, *messages]
        logger.info(
            "WindowedHistoryProvider: saved %d messages (total now %d)",
            len(messages), len(state["messages"]),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT SETUP — registered with the WindowedHistoryProvider
# ═══════════════════════════════════════════════════════════════════════════════

_thread_local = threading.local()


def _get_thread_agent():
    """Get or create agent + session for the current thread.

    The session is PERSISTENT across requests (unlike PathfinderIQ pattern).
    The WindowedHistoryProvider handles accumulation + windowing.
    """
    if not hasattr(_thread_local, "agent"):
        from agent_framework.azure import AzureAIAgentClient
        from agent_framework import AgentSession
        from azure.identity import DefaultAzureCredential

        client = AzureAIAgentClient(
            project_endpoint=PROJECT_ENDPOINT,
            model_deployment_name=MODEL_DEPLOYMENT,
            credential=DefaultAzureCredential(),
        )

        # Register the windowed provider — prevents auto-injection of
        # InMemoryHistoryProvider which would load ALL messages
        _thread_local.agent = client.as_agent(
            name="DemoAssistant",
            description="A helpful assistant demonstrating native context management",
            instructions=(
                "You are a helpful assistant. Answer questions clearly and concisely.\n"
                "You have conversation memory managed by a context provider.\n"
                "Always respond conversationally as if you remember the full conversation."
            ),
            tools=None,
            default_options={"model_id": MODEL_DEPLOYMENT},
            context_providers=[WindowedHistoryProvider(max_messages=MAX_CONTEXT_MESSAGES)],
        )

        # Persistent session — accumulates state across requests
        _thread_local.session = AgentSession()
        logger.info(
            "Agent built with WindowedHistoryProvider(max=%d) in thread %s",
            MAX_CONTEXT_MESSAGES, threading.current_thread().name,
        )

    return _thread_local.agent, _thread_local.session


# ═══════════════════════════════════════════════════════════════════════════════
# STREAMING
# ═══════════════════════════════════════════════════════════════════════════════

async def _run_agent_async(user_message: str):
    """Async generator: run agent with native context, yield text chunks.

    THE NATIVE PATTERN:
      - Reuse session (context provider manages history automatically)
      - Just pass the user message — no injection needed
      - The WindowedHistoryProvider.before_run() loads last N messages
      - The WindowedHistoryProvider.after_run() saves input + response
    """
    from agent_framework import AgentResponseUpdate

    agent, session = _get_thread_agent()

    async for update in agent.run(user_message, stream=True, session=session):
        if not isinstance(update, AgentResponseUpdate):
            continue
        for content in (update.contents or []):
            if hasattr(content, "text") and content.text:
                yield content.text


def run_agent_streaming(user_message: str):
    """Sync wrapper for async streaming."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        agen = _run_agent_async(user_message)
        while True:
            try:
                chunk = loop.run_until_complete(agen.__anext__())
                yield chunk
            except StopAsyncIteration:
                break
    finally:
        pass  # Don't close loop — aiohttp session needs it


# ═══════════════════════════════════════════════════════════════════════════════
# WEB UI — served from frontend/index.html (shared with pathfinderiq_style)
# ═══════════════════════════════════════════════════════════════════════════════

_FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "current_pathfinderiq_style", "frontend",
)


def _load_html() -> str:
    html_path = os.path.join(_FRONTEND_DIR, "index.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


class ChatHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/", ""):
            try:
                page = _load_html()
            except FileNotFoundError:
                self.send_error(500, "frontend/index.html not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(page.encode())
            except BrokenPipeError:
                pass
        elif self.path == "/config":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps({
                    "max_context_messages": MAX_CONTEXT_MESSAGES,
                    "model": MODEL_DEPLOYMENT,
                    "pattern": "native_context_provider",
                }).encode())
            except BrokenPipeError:
                pass
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/chat":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        user_message = body.get("message", "").strip()

        if not user_message:
            self.send_error(400, "Empty message")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        try:
            # No manual history tracking needed — the provider handles it
            for chunk in run_agent_streaming(user_message):
                self.wfile.write(f"data: {chunk}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except BrokenPipeError:
            logger.warning("Client disconnected mid-stream")

    def log_message(self, format, *args):
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    print(f"\n{'='*60}")
    print(f"  Foundry Agent — Native Context Provider Demo")
    print(f"  Model: {MODEL_DEPLOYMENT}")
    print(f"  Context window: last {MAX_CONTEXT_MESSAGES} messages")
    print(f"  Pattern: WindowedHistoryProvider (native SDK)")
    print(f"  Endpoint: {PROJECT_ENDPOINT[:60]}...")
    print(f"{'='*60}")
    print(f"\n  Open http://localhost:{PORT}\n")

    server = ThreadedHTTPServer(("0.0.0.0", PORT), ChatHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
