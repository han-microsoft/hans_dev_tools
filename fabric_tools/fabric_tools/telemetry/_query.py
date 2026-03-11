"""KQL telemetry query tool — executes KQL against Fabric Eventhouse.

Module role:
    Provides the ``query_telemetry`` tool function that the agent calls to
    query network alert and telemetry data. Uses the Azure Kusto SDK's
    KustoClient for direct KQL query execution against Fabric Eventhouse.

Design features:
    - Row-count guardrail: appends ``| take 1000`` if no limit clause found
    - Lazy-initialised KustoClient: avoids import-time failures if SDK absent
    - Datetime serialisation: converts datetime objects to ISO 8601 strings
    - Structured JSON output: {columns, rows} on success, {error, detail} on failure
    - Shared throttle gate: bounded concurrent requests via FabricThrottleGate

Available KQL tables (scenario-dependent):
    AlertStream    — network alerts (severity, source, timestamp)
    LinkTelemetry  — link performance metrics (latency, loss, utilisation)

Key collaborators:
    - ``azure.kusto.data.KustoClient`` — Kusto SDK client for KQL execution
    - ``tools/_fabric_constants.py`` — EVENTHOUSE_QUERY_URI, FABRIC_KQL_DB_NAME
    - ``tools/_fabric_throttle.py``  — shared semaphore + circuit breaker

Dependents:
    Imported by: ``tools/telemetry/__init__.py``
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Annotated, Any

from agent_framework import tool
from pydantic import Field

from fabric_tools._stubs import traced_tool
from fabric_tools._constants import KQL_MAX_ROWS
from fabric_tools._throttle import FabricThrottleError, get_fabric_gate

logger = logging.getLogger(__name__)

import asyncio as _asyncio

_kql_clients: dict[str, Any] = {}  # Cache KQL clients by query URI
_kql_lock = _asyncio.Lock()


def _get_fabric_telemetry_config() -> tuple[str, str]:
    """Resolve eventhouse_query_uri and kql_db_name from RequestScope.

    The three-tier resolution (scenario.yaml > env vars) is done once per
    request in build_request_scope(). This function reads pre-extracted values.

    Returns:
        Tuple of (eventhouse_query_uri, kql_db_name).
    """
    from fabric_tools._stubs import get_request_scope
    fc = get_request_scope().fabric_config
    return fc.eventhouse_query_uri, fc.kql_db_name

async def _get_kql_client(query_uri: str):
    """Get or create a KustoClient for the given query URI.

    Asyncio-safe. Caches clients by URI so switching scenarios reuses
    existing connections when the URI is the same.

    Uses the same credential priority as ``_fabric_auth._get_credential()``:
      1. FABRIC_TENANT_ID + FABRIC_CLIENT_ID + FABRIC_CLIENT_SECRET
         → ClientSecretCredential (cross-tenant SP)
      2. Running in Azure → DefaultAzureCredential (managed identity)
      3. Local dev → AzureCliCredential
    """
    if query_uri in _kql_clients:
        return _kql_clients[query_uri]

    async with _kql_lock:
        if query_uri in _kql_clients:
            return _kql_clients[query_uri]

        from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
        from fabric_tools._credentials import get_azure_credential

        # Use centralized credential factory — same 3-tier priority as Fabric auth
        cred = get_azure_credential(require_fabric_sp=True)

        kcsb = KustoConnectionStringBuilder.with_azure_token_credential(
            query_uri, cred
        )
        client = KustoClient(kcsb)
        _kql_clients[query_uri] = client
        return client


# ── Guardrails — delegated to shared module ──────────────────────────────────
from fabric_tools._guardrails import validate_read_only as _validate_read_only
from fabric_tools._guardrails import ensure_limit as _ensure_limit_shared


def _ensure_take_limit(query: str, max_rows: int = KQL_MAX_ROWS) -> str:
    """Delegate to shared guardrails module for KQL limit injection."""
    return _ensure_limit_shared(query, "kql", max_rows)


def _validate_kql_read_only(query: str) -> str | None:
    """Delegate to shared guardrails module for KQL read-only check."""
    return _validate_read_only(query, "kql")


@tool(approval_mode="never_require")
@traced_tool("query_telemetry", backend="fabric")
async def query_telemetry(
    query: Annotated[
        str,
        Field(
            description=(
                "KQL query against link performance and sensor data. "
                "Tables: LinkTelemetry (columns: LinkId, Timestamp, UtilizationPct, "
                "OpticalPowerDbm, BitErrorRate, LatencyMs), "
                "SensorReadings (columns: ReadingId, Timestamp, SensorId, SensorType, "
                "Value, Unit, Status). "
                "Do NOT query AlertStream here — use query_alerts instead. "
                "Example: LinkTelemetry | where LinkId == 'LINK-SYD-MEL-FIBRE-01' "
                "| top 10 by Timestamp desc"
            )
        ),
    ],
    **kwargs: Any,
) -> str:
    """Execute a KQL query against LinkTelemetry or SensorReadings.

    Returns JSON with {columns, rows} on success or {error, detail} on failure.
    For alert data (AlertStream), use ``query_alerts`` instead.
    """
    # Resolve eventhouse config from active scenario
    query_uri, db_name = _get_fabric_telemetry_config()
    if not query_uri or not db_name:
        return json.dumps(
            {
                "error": True,
                "detail": "Eventhouse not configured for this scenario (no eventhouse_query_uri or kql_db_name in services.fabric).",
            }
        )

    # Read-only guardrail — reject management commands before execution
    violation = _validate_kql_read_only(query)
    if violation:
        return json.dumps({"error": True, "detail": violation})

    safe_query = _ensure_take_limit(query)
    logger.info("query_telemetry: %s", safe_query[:200])
    gate = await get_fabric_gate()
    try:
        was_probe = await gate.acquire()
    except FabricThrottleError as e:
        return json.dumps({"error": True, "detail": str(e)})

    try:
        client = await _get_kql_client(query_uri)
        response = await asyncio.to_thread(client.execute, db_name, safe_query)
        primary = response.primary_results[0] if response.primary_results else None
        if primary is None:
            await gate.record_success()
            return json.dumps({"columns": [], "rows": []})

        columns = [
            {"name": c.column_name, "type": c.column_type} for c in primary.columns
        ]
        rows = []
        for row in primary:
            row_dict = {}
            for col in primary.columns:
                val = row[col.column_name]
                if hasattr(val, "isoformat"):
                    val = val.isoformat()
                row_dict[col.column_name] = val
            rows.append(row_dict)

        await gate.record_success()
        row_count = len(rows)
        logger.info("query_telemetry complete: %d rows", row_count)
        return json.dumps({"columns": columns, "rows": rows}, default=str)
    except Exception as e:
        # Log the full exception server-side for debugging, but never
        # expose raw text (may contain cluster URLs, connection strings)
        # to the agent/client.
        logger.exception("query_telemetry failed: %s", e)
        err_str = str(e).lower()
        if "429" in err_str or "throttl" in err_str:
            await gate.record_429()
        # Surface the actual KQL error (e.g. column not found) so the agent
        # can fix its query rather than retrying the same broken query.
        sanitized = _sanitize_error(str(e))
        return json.dumps({
            "error": True,
            "detail": f"Telemetry query failed: {sanitized}",
        })
    finally:
        gate.release(_was_probe=was_probe)


def _sanitize_error(msg: str) -> str:
    """Strip cluster URLs and connection strings, keep the KQL error visible."""
    sanitized = re.sub(r'https?://\S+', '[endpoint]', msg)
    return sanitized[:300]
