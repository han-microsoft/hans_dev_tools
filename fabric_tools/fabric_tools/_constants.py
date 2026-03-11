"""Fabric tool constants — env vars and tunables.

Module role:
    Centralises all configuration for the Fabric tools. Values are read
    from environment variables at import time. The tool modules (graph.py,
    telemetry.py, _throttle.py) import from here rather than calling
    ``os.getenv()`` directly.

Configuration groups:
    Fabric API     — base URL, workspace ID, graph model ID, scope
    Eventhouse/KQL — query URI, database name
    Throttle       — concurrency limit, circuit breaker thresholds
    GQL retry      — max retries for 429, ColdStartTimeout, continuations
    Row guardrails — max rows injected into queries without LIMIT/take
"""

import os

# ── Fabric API ───────────────────────────────────────────────────────────────
FABRIC_API_URL = os.getenv("FABRIC_API_URL", "https://api.fabric.microsoft.com/v1")  # Base URL for Fabric REST API
FABRIC_SCOPE = os.getenv("FABRIC_SCOPE", "https://api.fabric.microsoft.com/.default")  # OAuth scope for token acquisition
FABRIC_WORKSPACE_ID = os.getenv("FABRIC_WORKSPACE_ID", "")          # Fabric workspace GUID (from deploy.sh output)
FABRIC_GRAPH_MODEL_ID = os.getenv("FABRIC_GRAPH_MODEL_ID", "")      # Ontology graph model GUID (from deploy.sh output)

# ── Eventhouse / KQL ─────────────────────────────────────────────────────────
EVENTHOUSE_QUERY_URI = os.getenv("EVENTHOUSE_QUERY_URI", "")
FABRIC_KQL_DB_NAME = os.getenv("FABRIC_KQL_DB_NAME", "")

# ── Throttle / circuit breaker tunables ──────────────────────────────────────
# Minimum of 1 prevents operator typo (FABRIC_MAX_CONCURRENT=0) from creating
# a permanently-deadlocked asyncio.Semaphore(0).
FABRIC_MAX_CONCURRENT = max(1, int(os.getenv("FABRIC_MAX_CONCURRENT", "2")))  # F8 safe default
FABRIC_CB_THRESHOLD = int(os.getenv("FABRIC_CB_THRESHOLD", "3"))      # consecutive 429s to trip
FABRIC_CB_COOLDOWN = float(os.getenv("FABRIC_CB_COOLDOWN", "60"))     # initial cooldown (s)
FABRIC_CB_MAX_COOLDOWN = 300.0                                         # max cooldown cap (s)

# ── GQL retry tunables ───────────────────────────────────────────────────────
GQL_MAX_429_RETRIES = 2
GQL_MAX_COLDSTART_RETRIES = 5
GQL_MAX_CONTINUATION_RETRIES = 5
GQL_DEFAULT_429_WAIT = 30        # seconds
# Token staleness threshold — re-acquire token if older than this.
# Must match _TOKEN_STALE_SECS in _fabric_auth.py (2700s) to avoid
# holding a stale local copy while the auth module has already refreshed.
GQL_TOKEN_STALE_SECS = 2700     # ~45 min — unified with _fabric_auth._TOKEN_STALE_SECS

# ── Row-count guardrails ─────────────────────────────────────────────────────
GQL_MAX_ROWS = 500               # LIMIT clause injected if missing
KQL_MAX_ROWS = 1000              # | take N appended if missing
