#!/usr/bin/env python3
"""
foundry_agent_context_demo.py — Custom Context Management for Azure AI Foundry Agents

Demonstrates how to control exactly what conversation context a Foundry agent
sees, solving the problem: "How do I pass only the last N messages to my agent
instead of the full history?"

THE CORE TECHNIQUE:
  The Agent Framework SDK's agent.run(message, session=session) accepts a single
  string — not a messages array. The SDK manages its own server-side thread. If
  you reuse the session, the server accumulates ALL messages and you can't trim.

  Solution:
    1. Create a FRESH AgentSession() for every request (no server-side memory)
    2. Keep your own conversation history list
    3. Slice to last N messages (or apply any custom logic)
    4. Inject the sliced history into the user message as a <prior_conversation> block
    5. The agent sees exactly what you want — nothing more, nothing less

  This pattern works for ANY context management strategy:
    - Last N messages
    - Token-budgeted window
    - Summary-based compression
    - Relevance-filtered context

USAGE:
  # Set environment variables:
  export AZURE_AI_PROJECT_ENDPOINT="https://<your-foundry>.services.ai.azure.com/api/projects/<project>"
  export AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME="gpt-4.1"

  # Run (starts a local web UI on port 7860):
  python3 foundry_agent_context_demo.py

  # Or set custom context window:
  MAX_CONTEXT_MESSAGES=5 python3 foundry_agent_context_demo.py

REQUIREMENTS:
  uv sync && uv run python3 foundry_agent_context_demo.py

STRIPPED FROM: github.com/hanchoong/pathfinderiq_azure_native_agentic_graphs
  Original implementation: app/backend/app/services/llm/agent.py
  Context windowing: app/backend/app/services/conversation/_context.py
"""

from __future__ import annotations

import html
import json
import logging
import os
import sys
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("agent_context_demo")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

PROJECT_ENDPOINT = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "https://pulsenextfoundry.services.ai.azure.com/api/projects/proj-default")
MODEL_DEPLOYMENT = os.environ.get("AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME", "gpt-5-mini")
MAX_CONTEXT_MESSAGES = int(os.environ.get("MAX_CONTEXT_MESSAGES", "10"))
PORT = int(os.environ.get("PORT", "7860"))

if not PROJECT_ENDPOINT:
    print("ERROR: Set AZURE_AI_PROJECT_ENDPOINT environment variable.")
    print("  export AZURE_AI_PROJECT_ENDPOINT='https://<foundry>.services.ai.azure.com/api/projects/<project>'")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# CORE PATTERN: Context Injection for Foundry Agents
# ═══════════════════════════════════════════════════════════════════════════════

def build_context_injection(history: list[dict], user_message: str, max_messages: int) -> str:
    """Build augmented user message with injected conversation context.

    This is the key technique. The Foundry agent only sees a single string,
    so we encode the conversation history INTO the message itself.

    Args:
        history: Full conversation history [{role, content}, ...]
        user_message: The current user query
        max_messages: Maximum prior messages to include (YOUR context window)

    Returns:
        Augmented message string with <prior_conversation> block prepended
    """
    # Slice to last N messages (skip the current user message which is last)
    prior = history[-(max_messages + 1):-1] if len(history) > 1 else []

    if not prior:
        return user_message

    # Build a readable transcript
    lines = []
    for msg in prior:
        role = msg["role"].upper()
        content = msg["content"]
        # Truncate very long messages to save tokens
        if len(content) > 2000:
            content = content[:2000] + "..."
        lines.append(f"{role}: {content}")

    context_block = "\n".join(lines)

    return (
        f"<prior_conversation>\n"
        f"The following is the conversation history. Use it as context.\n\n"
        f"{context_block}\n"
        f"</prior_conversation>\n\n"
        f"{user_message}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT SETUP (PathfinderIQ pattern: agent_framework + as_agent + streaming)
# ═══════════════════════════════════════════════════════════════════════════════

# Thread-local storage — each worker thread gets its own client/agent/loop
# because aiohttp binds Futures to the event loop that created them.
import threading
_thread_local = threading.local()

AGENT_INSTRUCTIONS = (
    "You are a helpful assistant. Answer questions clearly and concisely.\n"
    "When the user references prior conversation, use the <prior_conversation> "
    "block in their message for context. Do not mention the block to the user.\n"
    "Always respond conversationally as if you remember the full conversation."
)


def _get_thread_agent():
    """Get or create client + agent for the current thread."""
    if not hasattr(_thread_local, "agent"):
        from agent_framework.azure import AzureAIAgentClient
        from azure.identity import DefaultAzureCredential

        client = AzureAIAgentClient(
            project_endpoint=PROJECT_ENDPOINT,
            model_deployment_name=MODEL_DEPLOYMENT,
            credential=DefaultAzureCredential(),
        )
        _thread_local.agent = client.as_agent(
            name="DemoAssistant",
            description="A helpful assistant demonstrating context management",
            instructions=AGENT_INSTRUCTIONS,
            tools=None,
            default_options={"model_id": MODEL_DEPLOYMENT},
        )
        logger.info("Agent built in thread %s", threading.current_thread().name)
    return _thread_local.agent


# ═══════════════════════════════════════════════════════════════════════════════
# STREAMING AGENT RUN (PathfinderIQ pattern: fresh session + context injection)
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio


async def _run_agent_async(user_message: str, history: list[dict]):
    """Async generator: run agent with custom context, yield text chunks.

    THE PATHFINDERIQ PATTERN:
      1. Fresh AgentSession() per request (no server-side thread memory)
      2. Inject sliced history into the user message
      3. Stream via agent.run(stream=True) — real token-by-token SSE
    """
    from agent_framework import AgentSession, AgentResponseUpdate

    augmented_message = build_context_injection(
        history, user_message, MAX_CONTEXT_MESSAGES
    )

    agent = _get_thread_agent()
    session = AgentSession()  # Fresh session = YOU control the context

    prior_count = min(len(history) - 1, MAX_CONTEXT_MESSAGES) if len(history) > 1 else 0
    logger.info(
        "Context: %d/%d prior messages injected (max_context=%d)",
        prior_count, max(0, len(history) - 1), MAX_CONTEXT_MESSAGES,
    )

    async for update in agent.run(augmented_message, stream=True, session=session):
        if not isinstance(update, AgentResponseUpdate):
            continue
        for content in (update.contents or []):
            if hasattr(content, "text") and content.text:
                yield content.text


def run_agent_streaming(user_message: str, history: list[dict]):
    """Sync wrapper: runs the async streaming generator, yields chunks.

    Each thread (from ThreadingHTTPServer) gets its own event loop since
    asyncio loops are not thread-safe.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        agen = _run_agent_async(user_message, history)
        while True:
            try:
                chunk = loop.run_until_complete(agen.__anext__())
                yield chunk
            except StopAsyncIteration:
                break
    finally:
        # Don't close the loop — aiohttp session may need it for cleanup
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# WEB UI — served from frontend/index.html
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory conversation history (per-process, demo only)
conversation_history: list[dict] = []

# Resolve frontend directory relative to this script
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")


def _load_html() -> str:
    """Load and template the frontend HTML."""
    html_path = os.path.join(_FRONTEND_DIR, "index.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


class ChatHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler: serves UI on GET /, runs agent on POST /chat."""

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

        # Add user message to history
        conversation_history.append({"role": "user", "content": user_message})

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        full_response = []

        try:
            for chunk in run_agent_streaming(user_message, conversation_history):
                full_response.append(chunk)
                self.wfile.write(f"data: {chunk}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except BrokenPipeError:
            logger.warning("Client disconnected mid-stream")

        # Add assistant response to history
        assistant_text = "".join(full_response)
        if assistant_text:
            conversation_history.append({"role": "assistant", "content": assistant_text})

        logger.info(
            "Turn complete: history=%d messages, context_window=%d, injected=%d",
            len(conversation_history),
            MAX_CONTEXT_MESSAGES,
            min(len(conversation_history) - 1, MAX_CONTEXT_MESSAGES),
        )

    def log_message(self, format, *args):
        pass  # Suppress default HTTP logs


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════════

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a separate thread so the UI stays responsive."""
    daemon_threads = True


def main():
    """Entrypoint for both `python3 foundry_agent_context_demo.py` and `uv run demo`."""
    print(f"\n{'='*60}")
    print(f"  Foundry Agent — Custom Context Demo")
    print(f"  Model: {MODEL_DEPLOYMENT}")
    print(f"  Context window: last {MAX_CONTEXT_MESSAGES} messages")
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
