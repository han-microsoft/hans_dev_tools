"""Stubs for PathfinderIQ dependencies not needed in standalone mode.

Replaces:
  - app.observability.traced_tool → noop decorator
  - app.foundation.request_scope.get_request_scope → env-var-based config
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# ── Noop traced_tool decorator (replaces OTel tracing) ───────────────────────

def traced_tool(name: str = "", **kwargs):
    """Noop decorator — replaces app.observability.traced_tool."""
    def _decorator(func):
        return func
    return _decorator


# ── Simplified RequestScope (replaces app.foundation.request_scope) ──────────

@dataclass(frozen=True)
class FabricServiceConfig:
    """Fabric resource IDs — read from env vars at call time."""
    workspace_id: str = ""
    graph_model_id: str = ""
    eventhouse_query_uri: str = ""
    kql_db_name: str = ""


@dataclass(frozen=True)
class RequestScope:
    """Simplified scope — reads from env vars instead of scenario.yaml."""
    fabric_config: FabricServiceConfig = field(default_factory=FabricServiceConfig)
    scenario_yaml: dict = field(default_factory=dict)
    graph_backend: str = "fabric"
    effective_backend: str = "fabric"


def get_request_scope() -> RequestScope:
    """Build scope from env vars (no scenario.yaml needed)."""
    return RequestScope(
        fabric_config=FabricServiceConfig(
            workspace_id=os.getenv("FABRIC_WORKSPACE_ID", ""),
            graph_model_id=os.getenv("FABRIC_GRAPH_MODEL_ID", ""),
            eventhouse_query_uri=os.getenv("EVENTHOUSE_QUERY_URI", ""),
            kql_db_name=os.getenv("FABRIC_KQL_DB_NAME", ""),
        ),
    )
