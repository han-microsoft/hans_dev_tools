"""KQL alert query tool — executes AlertStream queries against Fabric Eventhouse.

Module role:
    Provides the ``query_alerts`` tool function that the agent calls to
    query network alert data. Separated from ``query_telemetry`` (link
    metrics + sensor readings) to prevent column cross-contamination —
    AlertStream has columns (PacketLossPct, CPUUtilPct) that don't exist
    in LinkTelemetry, and the agent was mixing them when both schemas
    appeared in the same tool description.

    Shares all infrastructure with ``_fabric.py``: KQL client, throttle
    gate, guardrails, config resolution. Only the tool function and its
    @tool description differ.

Key collaborators:
    - ``tools/telemetry/_fabric.py`` — shared config, client, guardrails
    - ``tools/_fabric_throttle.py``  — shared semaphore + circuit breaker

Dependents:
    Imported by: ``tools/telemetry/__init__.py``
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any

from agent_framework import tool
from pydantic import Field

from fabric_tools._stubs import traced_tool
from fabric_tools._throttle import FabricThrottleError, get_fabric_gate
from fabric_tools.telemetry._query import (
    _get_fabric_telemetry_config,
    _get_kql_client,
    _validate_kql_read_only,
    _ensure_take_limit,
)

logger = logging.getLogger(__name__)


@tool(approval_mode="never_require")
@traced_tool("query_alerts", backend="fabric")
async def query_alerts(
    query: Annotated[
        str,
        Field(
            description=(
                "KQL query against the AlertStream table ONLY. "
                "Returns network alerts: severity, source node, alert type, timestamps. "
                "Columns: AlertId, Timestamp, SourceNodeId, SourceNodeType, AlertType, "
                "Severity, Description, OpticalPowerDbm, BitErrorRate, CPUUtilPct, PacketLossPct. "
                "Example: AlertStream | where SourceNodeId == 'LINK-SYD-MEL-FIBRE-01' "
                "| top 10 by Timestamp desc"
            )
        ),
    ],
    **kwargs: Any,
) -> str:
    """Execute a KQL query against AlertStream in the Fabric Eventhouse.

    Returns JSON with {columns, rows} on success or {error, detail} on failure.
    Applies row-count guardrail (max 1000 rows) automatically.

    This tool queries ONLY the AlertStream table. For link performance
    metrics (LinkTelemetry) and sensor readings (SensorReadings), use
    ``query_telemetry`` instead.
    """
    # Resolve eventhouse config from active scenario
    query_uri, db_name = _get_fabric_telemetry_config()
    if not query_uri or not db_name:
        return json.dumps({
            "error": True,
            "detail": "Eventhouse not configured for this scenario.",
        })

    # Read-only guardrail
    violation = _validate_kql_read_only(query)
    if violation:
        return json.dumps({"error": True, "detail": violation})

    safe_query = _ensure_take_limit(query)
    logger.info("query_alerts: %s", safe_query[:200])
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
        logger.info("query_alerts complete: %d rows", len(rows))
        return json.dumps({"columns": columns, "rows": rows}, default=str)
    except Exception as e:
        logger.exception("query_alerts failed: %s", e)
        err_str = str(e).lower()
        if "429" in err_str or "throttl" in err_str:
            await gate.record_429()
        return json.dumps({
            "error": True,
            "detail": f"Alert query failed: {_sanitize_error(str(e))}",
        })
    finally:
        gate.release(_was_probe=was_probe)


def _sanitize_error(msg: str) -> str:
    """Strip cluster URLs and connection strings from error messages.

    Keeps the KQL-level error (e.g. column not found) visible to the agent
    while removing infrastructure details.
    """
    # Remove anything that looks like a URL
    import re
    sanitized = re.sub(r'https?://\S+', '[endpoint]', msg)
    # Truncate to reasonable length
    return sanitized[:300]
