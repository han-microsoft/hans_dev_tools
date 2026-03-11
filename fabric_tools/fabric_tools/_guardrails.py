"""Shared query-language guardrails — read-only validation + limit injection.

Module role:
    Centralizes query safety guardrails used by graph and telemetry tool
    backends. Each backend delegates to the appropriate language-specific
    validator and limit injector rather than duplicating the logic.

Supported languages:
    gql     — Used by Fabric Graph Model backend (ISO GQL)
    kql     — Used by Fabric Eventhouse backend (Kusto Query Language)
    cypher  — Retained for completeness (not actively used)
    gremlin — Retained for completeness (not actively used)
    sql     — Retained for completeness (not actively used)

Key collaborators:
    - tools/graph_explorer/_fabric.py  — calls validate/ensure for gql
    - tools/telemetry/_fabric.py       — calls validate/ensure for kql

Dependents:
    Imported by: graph_explorer + telemetry backends
"""

from __future__ import annotations

import re

# ── Compiled regexes (once at import time) ───────────────────────────────────

# Cypher write keywords — shared by in-memory and Neo4j backends
_CYPHER_WRITE_RE = re.compile(
    r"\b(CREATE|DELETE|SET|MERGE|REMOVE|DETACH|DROP|CALL)\b",
    re.IGNORECASE,
)

# GQL write keywords — Fabric Graph Model (ISO GQL)
_GQL_WRITE_RE = re.compile(
    r"\b(CREATE|DELETE|SET|MERGE|INSERT|REMOVE|DETACH)\b",
    re.IGNORECASE,
)

# Cypher/GQL LIMIT detection
_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)

# KQL take/limit/top detection
_KQL_TAKE_RE = re.compile(r"\|\s*(take|limit|top)\s+\d+", re.IGNORECASE)

# Gremlin write steps — addV, addE, drop, property (mutation context)
_GREMLIN_WRITE_RE = re.compile(
    r"\.(addV|addE|drop|property)\s*\(",
    re.IGNORECASE,
)

# Gremlin limit/range detection
_GREMLIN_LIMIT_RE = re.compile(r"\.(limit|range)\s*\(", re.IGNORECASE)

# SQL (Cosmos NoSQL) write keywords
_SQL_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE)\b",
    re.IGNORECASE,
)

# SQL TOP / OFFSET LIMIT detection
_SQL_TOP_RE = re.compile(r"\bTOP\s+\d+", re.IGNORECASE)
_SQL_OFFSET_LIMIT_RE = re.compile(r"\bOFFSET\s+\d+\s+LIMIT\s+\d+", re.IGNORECASE)

# ── Default limits ───────────────────────────────────────────────────────────

DEFAULT_MAX_ROWS = {
    "cypher": 500,
    "gql": 500,
    "kql": 1000,
    "gremlin": 500,
    "sql": 500,
}

DEFAULT_MAX_QUERY_LENGTH = {
    "cypher": 2000,
    "gql": 5000,
    "kql": 5000,
    "gremlin": 5000,
    "sql": 5000,
}


def validate_read_only(query: str, language: str) -> str | None:
    """Validate that a query contains no write operations.

    Args:
        query: The raw query string to validate.
        language: Query language — "cypher", "gql", or "kql".

    Returns:
        Error message string if a violation is found, None if the query
        is safe for read-only execution.
    """
    max_len = DEFAULT_MAX_QUERY_LENGTH.get(language, 5000)
    if len(query) > max_len:
        return f"Query too long ({len(query)} chars). Maximum: {max_len}."

    if language == "cypher":
        match = _CYPHER_WRITE_RE.search(query)
        if match:
            return f"Write operations not allowed. Found: '{match.group()}'. Use read-only queries."

    elif language == "gql":
        if _GQL_WRITE_RE.search(query):
            return "Write operations (CREATE, DELETE, SET, MERGE, INSERT) are not permitted."

    elif language == "kql":
        stripped = query.strip()
        if stripped.startswith("."):
            return (
                "Management commands (.drop, .create, .alter, .set, .delete) "
                "are not permitted. Use data queries only."
            )

    elif language == "gremlin":
        match = _GREMLIN_WRITE_RE.search(query)
        if match:
            return f"Write operations not allowed. Found: '.{match.group(1)}()'. Use read-only traversals."

    elif language == "sql":
        match = _SQL_WRITE_RE.search(query)
        if match:
            return f"Write operations not allowed. Found: '{match.group()}'. Use SELECT queries only."

    return None


def ensure_limit(query: str, language: str, max_rows: int | None = None) -> str:
    """Append a row-count limit clause if the query lacks one.

    Prevents unbounded result sets from overwhelming the agent context window.

    Args:
        query: The query string.
        language: Query language — "cypher", "gql", or "kql".
        max_rows: Maximum rows to return. Uses per-language default if None.

    Returns:
        The query, possibly with an appended limit clause.
    """
    if max_rows is None:
        max_rows = DEFAULT_MAX_ROWS.get(language, 500)

    cleaned = query.rstrip().rstrip(";")

    if language in ("cypher", "gql"):
        # Cypher and GQL both use LIMIT keyword
        if _LIMIT_RE.search(query):
            return query
        return f"{cleaned} LIMIT {max_rows}"

    elif language == "kql":
        # KQL uses | take N
        if _KQL_TAKE_RE.search(query):
            return query
        return f"{cleaned} | take {max_rows}"

    elif language == "gremlin":
        # Gremlin uses .limit(N) or .range(start, end)
        if _GREMLIN_LIMIT_RE.search(query):
            return query
        return f"{cleaned}.limit({max_rows})"

    elif language == "sql":
        # Cosmos SQL uses SELECT TOP N or OFFSET x LIMIT y
        if _SQL_TOP_RE.search(query) or _SQL_OFFSET_LIMIT_RE.search(query):
            return query
        # Inject TOP after SELECT
        return re.sub(
            r"(?i)^(SELECT)\s+",
            rf"\1 TOP {max_rows} ",
            cleaned,
            count=1,
        )

    # Unknown language — return unchanged
    return query
