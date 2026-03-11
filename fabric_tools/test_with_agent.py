#!/usr/bin/env python3
"""
test_with_agent.py — Test Fabric tools WITH an Azure AI Foundry agent

Demonstrates a minimal agent that uses query_graph and query_telemetry as tools.
The agent decides WHEN and HOW to call the tools based on the user's question.

USAGE:
  export AZURE_AI_PROJECT_ENDPOINT="https://<foundry>.services.ai.azure.com/api/projects/<project>"
  export AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME="gpt-4.1"
  uv run python3 test_with_agent.py

REQUIRES:
  .env file with Fabric credentials + Azure AI Foundry access
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from dotenv import load_dotenv
load_dotenv()

# Check Foundry credentials
PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
MODEL = os.getenv("AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME", "gpt-4.1")

if not PROJECT_ENDPOINT:
    print("ERROR: Set AZURE_AI_PROJECT_ENDPOINT to use agent mode.")
    print("  export AZURE_AI_PROJECT_ENDPOINT='https://<foundry>.services.ai.azure.com/api/projects/<project>'")
    print("\nYou can still test tools directly: uv run python3 test_tools.py")
    sys.exit(1)


async def main():
    from agent_framework.azure import AzureAIAgentClient
    from agent_framework import AgentSession, AgentResponseUpdate
    from azure.identity import DefaultAzureCredential

    from fabric_tools import query_graph, query_telemetry, query_alerts

    print("=" * 70)
    print("  Fabric Tools + Agent — End-to-End Test")
    print(f"  Model: {MODEL}")
    print(f"  Endpoint: {PROJECT_ENDPOINT[:50]}...")
    print("=" * 70)

    # Build the agent with Fabric tools
    client = AzureAIAgentClient(
        project_endpoint=PROJECT_ENDPOINT,
        model_deployment_name=MODEL,
        credential=DefaultAzureCredential(),
    )

    agent = client.as_agent(
        name="NetworkAnalyst",
        description="Analyzes telecom network topology and telemetry",
        instructions=(
            "You are a network operations analyst. You have access to:\n"
            "- query_graph: Query the network graph topology (nodes, links, sensors) using GQL\n"
            "- query_telemetry: Query network performance metrics using KQL\n"
            "- query_alerts: Query network alerts using KQL\n\n"
            "When asked about the network, use these tools to find real data.\n"
            "Always show the data you found in your response."
        ),
        tools=[query_graph, query_telemetry, query_alerts],
        default_options={"model_id": MODEL},
    )

    # Test questions — agent decides which tools to call
    questions = [
        "Show me the first 3 network links in the topology",
        "What are the 3 most recent network alerts?",
    ]

    for i, question in enumerate(questions, 1):
        print(f"\n{'─' * 60}")
        print(f"  USER: {question}")
        print(f"{'─' * 60}")

        session = AgentSession()
        response_text = []

        async for update in agent.run(question, stream=True, session=session):
            if not isinstance(update, AgentResponseUpdate):
                continue
            for content in (update.contents or []):
                if hasattr(content, "text") and content.text:
                    response_text.append(content.text)
                    print(content.text, end="", flush=True)

        print()  # newline after streaming
        if not response_text:
            print("  [No response]")

    print(f"\n{'=' * 70}")
    print("  Agent tests complete")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
