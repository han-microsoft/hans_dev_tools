"""Fabric Graph Model ontology discovery — fetch vertex/edge types + properties.

Provides get_ontology() which calls the Fabric getDefinition API to retrieve
the full graph schema. Returns a structured summary of node types, edge types,
their properties, and example GQL queries.

This is designed to be called once at startup or injected into agent prompts
so the agent knows what entities exist and how to query them.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any

import httpx

from fabric_tools._auth import get_fabric_token
from fabric_tools._constants import FABRIC_API_URL, FABRIC_WORKSPACE_ID, FABRIC_GRAPH_MODEL_ID

logger = logging.getLogger(__name__)


async def get_ontology() -> dict[str, Any]:
    """Fetch the graph model ontology from Fabric's getDefinition API.

    Returns a dict with:
      - node_types: [{label, properties: [{name, type}], primary_key}]
      - edge_types: [{label, source_label, target_label, properties}]
      - summary: human-readable text for agent prompts

    The API is async (202 → poll → result), so this takes 3-5 seconds.
    """
    workspace_id = FABRIC_WORKSPACE_ID or os.getenv("FABRIC_WORKSPACE_ID", "")
    graph_model_id = FABRIC_GRAPH_MODEL_ID or os.getenv("FABRIC_GRAPH_MODEL_ID", "")

    if not workspace_id or not graph_model_id:
        return {"error": "FABRIC_WORKSPACE_ID or FABRIC_GRAPH_MODEL_ID not set"}

    token = await get_fabric_token()
    headers = {"Authorization": f"Bearer {token}"}
    base = f"{FABRIC_API_URL}/workspaces/{workspace_id}/GraphModels/{graph_model_id}"

    async with httpx.AsyncClient(timeout=60) as client:
        # 1. Initiate getDefinition (async operation)
        r = await client.post(f"{base}/getDefinition", headers=headers)
        if r.status_code not in (200, 202):
            return {"error": f"getDefinition failed: {r.status_code} {r.text[:200]}"}

        if r.status_code == 202:
            location = r.headers.get("Location", "")
            if not location:
                return {"error": "getDefinition returned 202 but no Location header"}

            # 2. Poll until complete
            for _ in range(15):
                await asyncio.sleep(2)
                r2 = await client.get(location, headers=headers)
                if r2.status_code == 200:
                    op = r2.json()
                    if op.get("status") == "Succeeded":
                        break
            else:
                return {"error": "getDefinition timed out (30s)"}

            # 3. Fetch result
            r3 = await client.get(f"{location}/result", headers=headers)
            if r3.status_code != 200:
                return {"error": f"getDefinition result failed: {r3.status_code}"}
            result = r3.json()
        else:
            result = r.json()

    # 4. Parse the definition parts
    parts = {}
    for part in result.get("definition", {}).get("parts", []):
        path = part.get("path", "")
        payload = part.get("payload", "")
        if payload:
            parts[path] = json.loads(base64.b64decode(payload).decode("utf-8"))

    graph_type = parts.get("graphType.json", {})
    graph_def = parts.get("graphDefinition.json", {})

    # 5. Build alias → label mapping
    alias_to_label = {}
    node_types = []
    for nt in graph_type.get("nodeTypes", []):
        label = nt["labels"][0] if nt.get("labels") else nt.get("alias", "?")
        alias_to_label[nt.get("alias", "")] = label
        node_types.append({
            "label": label,
            "primary_key": nt.get("primaryKeyProperties", []),
            "properties": [
                {"name": p["name"], "type": p.get("type", "STRING")}
                for p in nt.get("properties", [])
            ],
        })

    edge_types = []
    for et in graph_type.get("edgeTypes", []):
        label = et["labels"][0] if et.get("labels") else et.get("alias", "?")
        src_alias = et.get("sourceNodeType", {}).get("alias", "")
        tgt_alias = et.get("destinationNodeType", {}).get("alias", "")
        edge_types.append({
            "label": label,
            "source_label": alias_to_label.get(src_alias, src_alias),
            "target_label": alias_to_label.get(tgt_alias, tgt_alias),
            "properties": [
                {"name": p["name"], "type": p.get("type", "STRING")}
                for p in et.get("properties", [])
            ],
        })

    # 6. Build human-readable summary for agent prompts
    lines = ["## Graph Ontology\n"]
    lines.append("### Node Types (Vertices)")
    for nt in node_types:
        props = ", ".join(f"{p['name']}:{p['type']}" for p in nt["properties"])
        pk = ", ".join(nt["primary_key"])
        lines.append(f"- **{nt['label']}** (PK: {pk}) — {props}")

    lines.append("\n### Edge Types (Relationships)")
    for et in edge_types:
        props = ", ".join(f"{p['name']}:{p['type']}" for p in et["properties"]) or "no properties"
        lines.append(f"- **{et['label']}**: {et['source_label']} → {et['target_label']} ({props})")

    lines.append("\n### GQL Query Syntax (ISO GQL, NOT Gremlin)")
    lines.append("Use MATCH/RETURN pattern. Property access: n.PropertyName")
    lines.append("```")
    if node_types:
        first = node_types[0]
        pk = first["primary_key"][0] if first["primary_key"] else "id"
        lines.append(f"-- List {first['label']}s:")
        lines.append(f"MATCH (n:{first['label']}) RETURN n LIMIT 10")
    if edge_types:
        e = edge_types[0]
        lines.append(f"\n-- Traverse {e['source_label']} → {e['target_label']} via {e['label']}:")
        lines.append(f"MATCH (a:{e['source_label']})-[r:{e['label']}]->(b:{e['target_label']}) RETURN a, r, b LIMIT 10")
    lines.append("```")
    lines.append("\n**Important**: RETURN n returns the vertex ID. Property projections like n.Name may return empty — use RETURN n for full vertex data.")

    summary = "\n".join(lines)

    return {
        "node_types": node_types,
        "edge_types": edge_types,
        "summary": summary,
    }


def get_ontology_sync() -> dict[str, Any]:
    """Synchronous wrapper for get_ontology()."""
    return asyncio.run(get_ontology())
