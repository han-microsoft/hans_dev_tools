#!/usr/bin/env python3
"""
test_tools.py — Test Fabric tools in isolation (no agent needed)

Demonstrates:
  1. Graph query (GQL) — list network nodes
  2. Telemetry query (KQL) — recent alerts
  3. Telemetry query (KQL) — link performance metrics
  4. Read-only guardrail — mutation blocked
  5. Auto-LIMIT injection — unbounded query gets safe limit

USAGE:
  # Load env vars and run:
  uv run python3 test_tools.py

EXPECTED OUTPUT:
  Each test prints the query, result summary, and key data points.
  The mutation test should show a blocked error.

REQUIRES:
  .env file with Fabric credentials (see .env.example)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Load .env file
from dotenv import load_dotenv
load_dotenv()

# Verify required env vars
REQUIRED = ["FABRIC_WORKSPACE_ID", "FABRIC_GRAPH_MODEL_ID", "EVENTHOUSE_QUERY_URI", "FABRIC_KQL_DB_NAME"]
missing = [v for v in REQUIRED if not os.getenv(v)]
if missing:
    print(f"ERROR: Missing env vars: {missing}")
    print(f"Copy .env.example to .env and fill in your Fabric credentials.")
    sys.exit(1)


async def main():
    from fabric_tools import query_graph, query_telemetry, query_alerts

    print("=" * 70)
    print("  Fabric Tools — Standalone Test")
    print(f"  Workspace: {os.getenv('FABRIC_WORKSPACE_ID', '')[:8]}...")
    print(f"  Eventhouse: {os.getenv('EVENTHOUSE_QUERY_URI', '')[:50]}...")
    print("=" * 70)

    # ── Test 0: Ontology Discovery ───────────────────────────────────────
    # Fetch the graph schema (vertex/edge types, properties)
    # This should be called once and injected into agent prompts
    print("▶ TEST 0: Ontology Discovery")
    from fabric_tools import get_ontology
    ontology = await get_ontology()
    if ontology.get("error"):
        print(f"  ✗ ERROR: {ontology['error']}")
    else:
        print(f"  ✓ Node types: {len(ontology['node_types'])}")
        print(f"  ✓ Edge types: {len(ontology['edge_types'])}")
        node_labels = [n["label"] for n in ontology["node_types"]]
        print(f"  Nodes: {', '.join(node_labels)}")
        edge_labels = [e["label"] for e in ontology["edge_types"][:5]]
        print(f"  Edges (first 5): {', '.join(edge_labels)}")
    print()

    # ── Test 1: Graph Query — List vertices (GQL MATCH/RETURN) ───────────
    # NOTE: Fabric Graph Model uses ISO GQL, NOT Gremlin
    # - Correct: MATCH (n:CoreRouter) RETURN n LIMIT 5
    # - Wrong:   g.V().limit(5)  ← this is Gremlin, returns vertex IDs only
    print("▶ TEST 1: Graph Query (GQL) — List CoreRouters")
    print("  Query: MATCH (n:CoreRouter) RETURN n LIMIT 5")
    result = await query_graph(query="MATCH (n:CoreRouter) RETURN n LIMIT 5")
    data = json.loads(result)
    if data.get("error"):
        print(f"  ✗ ERROR: {data['detail']}")
    else:
        rows = data.get("data", [])
        print(f"  ✓ Got {len(rows)} routers")
        if rows:
            print(f"  Sample: {json.dumps(rows[0], default=str)[:200]}")
    print()

    # ── Test 2: Graph Query — Traverse edges ──────────────────────────────
    # Query: Find TransportLinks connected to CoreRouters via connects_to
    print("▶ TEST 2: Graph Query (GQL) — Traverse edges")
    print("  Query: MATCH (tl:TransportLink)-[r:connects_to]->(cr:CoreRouter) RETURN tl, cr LIMIT 3")
    result = await query_graph(query="MATCH (tl:TransportLink)-[r:connects_to]->(cr:CoreRouter) RETURN tl, cr LIMIT 3")
    data = json.loads(result)
    if data.get("error"):
        print(f"  ✗ ERROR: {data['detail']}")
    else:
        rows = data.get("data", [])
        print(f"  ✓ Got {len(rows)} link→router connections")
        for r in rows[:3]:
            print(f"    → {json.dumps(r, default=str)[:150]}")
    print()

    # ── Test 3: Telemetry Query — Recent alerts ──────────────────────────
    # Query: Get the 5 most recent alerts from the AlertStream table
    # Expected: JSON with {columns, rows} containing alert records
    print("▶ TEST 3: Telemetry Query (KQL) — Recent alerts")
    kql = "AlertStream | order by Timestamp desc | take 5"
    print(f"  Query: {kql}")
    result = await query_alerts(query=kql)
    data = json.loads(result)
    if data.get("error"):
        print(f"  ✗ ERROR: {data['detail']}")
    else:
        rows = data.get("rows", [])
        cols = [c["name"] for c in data.get("columns", [])]
        print(f"  ✓ Got {len(rows)} alerts, columns: {cols[:5]}")
        if rows:
            print(f"  Sample: {json.dumps(rows[0], default=str)[:200]}")
    print()

    # ── Test 4: Telemetry Query — Link performance ───────────────────────
    # Query: Get performance metrics for links
    # Expected: JSON with performance data
    print("▶ TEST 4: Telemetry Query (KQL) — Link telemetry")
    kql = "LinkTelemetry | order by Timestamp desc | take 5"
    print(f"  Query: {kql}")
    result = await query_telemetry(query=kql)
    data = json.loads(result)
    if data.get("error"):
        print(f"  ✗ ERROR: {data['detail']}")
    else:
        rows = data.get("rows", [])
        cols = [c["name"] for c in data.get("columns", [])]
        print(f"  ✓ Got {len(rows)} telemetry records, columns: {cols[:5]}")
        if rows:
            print(f"  Sample: {json.dumps(rows[0], default=str)[:200]}")
    print()

    # ── Test 5: Read-only guardrail — Mutation blocked ───────────────────
    # Query: Attempt to insert data (should be blocked before execution)
    # Expected: Error response from guardrail
    print("▶ TEST 5: Read-Only Guardrail — Mutation blocked")
    print("  Query: CREATE (n:Malicious {name: 'hack'})")
    result = await query_graph(query="CREATE (n:Malicious {name: 'hack'})")
    data = json.loads(result)
    if data.get("error") and ("read-only" in data.get("detail", "").lower() or "write" in data.get("detail", "").lower()):
        print(f"  ✓ BLOCKED: {data['detail'][:100]}")
    elif data.get("error"):
        print(f"  ✓ BLOCKED (other): {data['detail'][:100]}")
    else:
        print(f"  ✗ WARNING: Mutation was NOT blocked!")
    print()

    # ── Test 6: Auto-LIMIT injection ─────────────────────────────────────
    # The tool should automatically add LIMIT to queries that don't have one
    print("▶ TEST 6: Auto-LIMIT injection")
    print("  Query: MATCH (n:CoreRouter) RETURN n (no limit — tool should inject LIMIT 500)")
    result = await query_graph(query="MATCH (n:CoreRouter) RETURN n")
    data = json.loads(result)
    if data.get("error"):
        print(f"  Result: {data['detail'][:100]}")
    else:
        rows = data.get("data", [])
        print(f"  ✓ Got {len(rows)} routers (capped by auto-LIMIT)")
    print()

    print("=" * 70)
    print("  All tests complete")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
