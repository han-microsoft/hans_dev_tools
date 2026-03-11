"""GQL topology query tool — executes ISO GQL against Fabric Graph Model.

Module role:
    Provides the ``query_graph`` tool function that the agent calls to query
    the network topology ontology graph. Supports ISO GQL syntax
    (MATCH/RETURN/WHERE/LIMIT) against the Fabric Graph Model REST API.

Design features:
    - Row-count guardrail: injects ``LIMIT 500`` if the query lacks one
    - Full retry logic: HTTP 429 (capacity), ColdStartTimeout (graph waking),
      continuation tokens (paginated large results)
    - Semaphore release/re-acquire around retry sleeps (prevents starvation)
    - Token refresh logic: re-acquires Fabric token if >50 minutes old
    - Structured JSON output: {columns, data} on success, {error, detail} on failure

Key collaborators:
    - ``tools/_fabric_auth.py``      – ``get_fabric_token()`` for Entra ID authentication
    - ``tools/_fabric_constants.py`` – workspace ID, graph model ID, retry limits
    - ``tools/_fabric_throttle.py``  – shared semaphore + circuit breaker
    - ``httpx``         – async HTTP client for Fabric REST API calls

Dependents:
    Imported by: ``tools/graph_explorer/__init__.py``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from typing import Annotated, Any

import httpx
from agent_framework import tool
from pydantic import Field

from fabric_tools._stubs import traced_tool
from fabric_tools._auth import get_fabric_token
from fabric_tools._constants import (
    FABRIC_API_URL,
    GQL_DEFAULT_429_WAIT,
    GQL_MAX_429_RETRIES,
    GQL_MAX_COLDSTART_RETRIES,
    GQL_MAX_CONTINUATION_RETRIES,
    GQL_MAX_ROWS,
    GQL_TOKEN_STALE_SECS,
)
from fabric_tools._throttle import FabricThrottleError, get_fabric_gate

logger = logging.getLogger(__name__)


# ── Per-request config resolution ────────────────────────────────────────────


def _get_fabric_ids_from_scenario() -> tuple[str, str]:
    """Resolve Fabric workspace_id and graph_model_id from RequestScope.

    The three-tier resolution (services.fabric > backends.graph_config.fabric >
    env vars) is done once per request in build_request_scope(). This function
    just reads the pre-extracted values.

    Returns:
        Tuple of (workspace_id, graph_model_id). Either may be empty.
    """
    from fabric_tools._stubs import get_request_scope
    fc = get_request_scope().fabric_config
    return fc.workspace_id, fc.graph_model_id


# ── Guardrails — delegated to shared module ──────────────────────────────────
from fabric_tools._guardrails import validate_read_only as _validate_read_only
from fabric_tools._guardrails import ensure_limit as _ensure_limit_shared


def _ensure_limit(query: str, max_rows: int = GQL_MAX_ROWS) -> str:
    """Delegate to shared guardrails module for GQL LIMIT injection."""
    return _ensure_limit_shared(query, "gql", max_rows)


def _validate_gql_read_only(query: str) -> str | None:
    """Delegate to shared guardrails module for GQL read-only check."""
    return _validate_read_only(query, "gql")


def _parse_retry_after(response: httpx.Response, default: int = 30) -> int:
    """Parse Retry-After header from a 429 response."""
    raw = response.headers.get("Retry-After", "")
    try:
        val = int(raw)
        return val if 0 < val <= 120 else default
    except (ValueError, TypeError):
        return default


# ── Tool function ────────────────────────────────────────────────────────────

@tool(approval_mode="never_require")
@traced_tool("query_graph", backend="fabric")
async def query_graph(
    query: Annotated[
        str,
        Field(
            description=(
                "GQL query against the network topology graph. Uses ISO GQL "
                "MATCH/RETURN syntax. Example: MATCH (r:CoreRouter) RETURN "
                "r.RouterId, r.Hostname. Relationships: MATCH "
                "(a)-[r:connects_to]->(b) RETURN a.RouterId, b.RouterId."
            )
        ),
    ],
    **kwargs: Any,
) -> str:
    """Execute a GQL query against the Fabric Graph Model.

    Returns JSON with {columns, data} on success or {error, detail} on failure.
    Applies LIMIT guardrail (max 500 rows) automatically.
    """
    # Read workspace and graph model IDs from the scenario's graph_config
    # block (Phase A fix ❌6-7). Previously read from os.environ which was
    # globally mutated by apply_graph_config() during scenario switches —
    # causing cross-user bleed. Now reads from the per-request scenario's
    # configuration via get_scenario_backends().
    workspace_id, graph_model_id = _get_fabric_ids_from_scenario()

    if not workspace_id or not graph_model_id:
        return json.dumps(
            {
                "error": True,
                "detail": "FABRIC_WORKSPACE_ID or FABRIC_GRAPH_MODEL_ID not configured.",
            }
        )

    # Read-only guardrail — reject write operations before they reach Fabric
    violation = _validate_gql_read_only(query)
    if violation:
        return json.dumps({"error": True, "detail": violation})

    safe_query = _ensure_limit(query)
    logger.info("query_graph: %s", safe_query[:200])
    url = (
        f"{FABRIC_API_URL}/workspaces/{workspace_id}"
        f"/GraphModels/{graph_model_id}/executeQuery?beta=true"
    )

    gate = await get_fabric_gate()
    # Mutable container tracking whether the gate is currently held by us.
    # Shared between outer scope and _execute_gql_inner so the inner retry
    # loop's release/reacquire cycles keep this state consistent.
    gate_state = {"held": False, "was_probe": False}
    try:
        was_probe = await gate.acquire()
        gate_state["held"] = True
        gate_state["was_probe"] = was_probe
    except FabricThrottleError as e:
        return json.dumps({"error": True, "detail": str(e)})

    try:
        return await _execute_gql_inner(safe_query, url, gate, gate_state=gate_state)
    except Exception as e:
        logger.exception("query_graph failed")
        return json.dumps({"error": True, "detail": str(e)})
    finally:
        # Only release if the gate is still held — _execute_gql_inner may
        # have released it during a retry and failed to reacquire.
        if gate_state["held"]:
            gate.release(_was_probe=gate_state["was_probe"])
            gate_state["held"] = False


# ── Inner retry loop ─────────────────────────────────────────────────────────


async def _execute_gql_inner(
    query: str, url: str, gate, *, gate_state: dict
) -> str:
    """Inner retry loop. Releases/re-acquires semaphore around sleeps.

    Uses ``gate_state`` (mutable dict) to track whether the gate is currently
    held. The outer ``query_graph`` reads ``gate_state[\"held\"]`` in its
    ``finally`` block to avoid double-release.
    """
    token = await get_fabric_token()
    token_acquired_at = time.monotonic()

    max_attempts = max(
        GQL_MAX_429_RETRIES, GQL_MAX_COLDSTART_RETRIES, GQL_MAX_CONTINUATION_RETRIES
    )
    retries_429 = 0
    retries_coldstart = 0
    retries_continuation = 0
    continuation_token: str | None = None

    async with httpx.AsyncClient(timeout=120.0) as client:
        for _attempt in range(max_attempts + 1):
            payload: dict = {"query": query}
            if continuation_token:
                payload["continuationToken"] = continuation_token

            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

            # ── HTTP 429: capacity throttled ──────────────────────
            if response.status_code == 429:
                retries_429 += 1
                await gate.record_429()
                if retries_429 > GQL_MAX_429_RETRIES:
                    return json.dumps(
                        {
                            "error": True,
                            "detail": "Fabric capacity exhausted — too many 429s.",
                        }
                    )
                wait = _parse_retry_after(response, GQL_DEFAULT_429_WAIT) * random.uniform(0.75, 1.25)
                logger.warning(
                    "Fabric 429 — retrying in %.0fs (%d/%d)",
                    wait, retries_429, GQL_MAX_429_RETRIES,
                )
                try:
                    gate.release(_was_probe=gate_state["was_probe"])
                finally:
                    gate_state["held"] = False
                    gate_state["was_probe"] = False
                await asyncio.sleep(wait)
                new_probe = await gate.acquire()
                gate_state["held"] = True
                gate_state["was_probe"] = new_probe
                continue

            # ── HTTP 500: check ColdStartTimeout ──────────────────
            if response.status_code == 500:
                body = (
                    response.json()
                    if "application/json" in response.headers.get("content-type", "")
                    else {}
                )
                if body.get("errorCode") == "ColdStartTimeout":
                    retries_coldstart += 1
                    if retries_coldstart > GQL_MAX_COLDSTART_RETRIES:
                        return json.dumps(
                            {
                                "error": True,
                                "detail": "Fabric GQL cold start — retries exhausted. "
                                "Try again in a minute.",
                            }
                        )
                    wait = min(10 * (2 ** (retries_coldstart - 1)), 60) * random.uniform(0.75, 1.25)
                    logger.warning(
                        "ColdStartTimeout — retrying in %.0fs (%d/%d)",
                        wait, retries_coldstart, GQL_MAX_COLDSTART_RETRIES,
                    )
                    continuation_token = None
                    try:
                        gate.release(_was_probe=gate_state["was_probe"])
                    finally:
                        gate_state["held"] = False
                        gate_state["was_probe"] = False
                    await asyncio.sleep(wait)
                    new_probe = await gate.acquire()
                    gate_state["held"] = True
                    gate_state["was_probe"] = new_probe
                    if time.monotonic() - token_acquired_at > GQL_TOKEN_STALE_SECS:
                        token = await get_fabric_token()
                        token_acquired_at = time.monotonic()
                    continue

                # Non-ColdStartTimeout 5xx — fail
                await gate.record_server_error()
                return json.dumps(
                    {
                        "error": True,
                        "detail": f"Fabric GQL failed (500): {response.text[:500]}",
                    }
                )

            # ── Other non-200 ─────────────────────────────────────
            if response.status_code != 200:
                await gate.record_server_error()
                return json.dumps(
                    {
                        "error": True,
                        "detail": f"Fabric GQL failed (HTTP {response.status_code}): "
                        f"{response.text[:500]}",
                    }
                )

            # ── 200 OK ────────────────────────────────────────────
            body = response.json()
            status_code = body.get("status", {}).get("code", "")
            result = body.get("result", body)

            # Status 02000 = cold-start continuation
            if status_code == "02000" and result.get("nextPage"):
                retries_continuation += 1
                if retries_continuation > GQL_MAX_CONTINUATION_RETRIES:
                    return json.dumps(
                        {
                            "error": True,
                            "detail": "Fabric GQL continuation retries exhausted.",
                        }
                    )
                continuation_token = result["nextPage"]
                logger.info(
                    "GQL cold start (02000) — continuation retry %d/%d",
                    retries_continuation,
                    GQL_MAX_CONTINUATION_RETRIES,
                )
                try:
                    gate.release(_was_probe=gate_state["was_probe"])
                finally:
                    gate_state["held"] = False
                    gate_state["was_probe"] = False
                await asyncio.sleep(10)
                new_probe = await gate.acquire()
                gate_state["held"] = True
                gate_state["was_probe"] = new_probe
                continue

            await gate.record_success()
            row_count = len(result.get("data", []))
            logger.info("query_graph complete: %d rows", row_count)
            return json.dumps(
                {
                    "columns": result.get("columns", []),
                    "data": result.get("data", []),
                },
                default=str,
            )

    return json.dumps(
        {"error": True, "detail": "GQL query failed — all retry attempts exhausted."}
    )


# ── Health check ─────────────────────────────────────────────────────────────


async def check_connectivity() -> dict[str, Any]:
    """Check Fabric Graph Model connectivity with a lightweight API call.

    Verifies the Graph Model endpoint responds by fetching its metadata.
    Does NOT run a query — just confirms the workspace and model IDs are valid
    and the authentication token works.

    Returns:
        Dict with 'ok' (bool) and 'detail' (str).

    Side effects:
        Network call to Fabric REST API.

    Dependents:
        Called by: routers/backends.py (connectivity check on switch)
    """
    # Dynamic read from scenario config — same rationale as query_graph above.
    workspace_id, graph_model_id = _get_fabric_ids_from_scenario()

    if not workspace_id or not graph_model_id:
        return {"ok": False, "detail": "FABRIC_WORKSPACE_ID or FABRIC_GRAPH_MODEL_ID not configured"}
    try:
        token = await get_fabric_token()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{FABRIC_API_URL}/workspaces/{workspace_id}/GraphModels/{graph_model_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                return {"ok": True, "detail": "Fabric Graph Model accessible"}
            return {"ok": False, "detail": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:300]}
