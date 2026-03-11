"""Fabric API authentication — token acquisition + caching.

Module role:
    Provides a cached Fabric API token for graph and telemetry tools.
    Uses DefaultAzureCredential in hosted environments (App Service, AKS)
    and AzureCliCredential for local development (``az login``).

    Token is cached and re-acquired after 45 minutes (2700 seconds) to
    avoid expiry mid-request. Azure Entra ID tokens expire after 60–90 minutes,
    so 45-minute refresh provides margin.

Thread safety:
    Token caching uses module-level globals. In an async context (single-thread
    event loop), this is safe. For multi-process deployments, each process
    maintains its own cache.

Key collaborators:
    - ``_fabric_constants.py`` – provides FABRIC_SCOPE for token request
    - ``graph_explorer/_fabric.py`` – calls ``get_fabric_token()`` for GQL API auth
    - ``telemetry/_fabric.py``     – uses DefaultAzureCredential directly (via KustoClient)

Dependents:
    Called by: ``graph_explorer/_fabric.py::_execute_gql_inner()``
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from azure.identity import AzureCliCredential, ClientSecretCredential, DefaultAzureCredential

from fabric_tools._constants import FABRIC_SCOPE

logger = logging.getLogger(__name__)

_credential = None
_cached_token: str | None = None
_token_acquired_at = 0.0
_TOKEN_STALE_SECS = 2700  # re-acquire after 45 min
_token_lock = asyncio.Lock()  # Prevents concurrent token refresh races


def _get_credential():
    """Pick credential based on environment. Delegates to centralized factory.

    Returns:
        A ``TokenCredential`` instance for Fabric API calls.
    """
    global _credential
    if _credential is None:
        from fabric_tools._credentials import get_azure_credential
        _credential = get_azure_credential(require_fabric_sp=True)
    return _credential


async def get_fabric_token() -> str:
    """Acquire a Fabric API token, using cache if still fresh.

    Uses double-checked locking via ``_token_lock`` to prevent multiple
    concurrent coroutines from refreshing the token simultaneously.
    """
    global _cached_token, _token_acquired_at
    now = time.monotonic()
    # Fast path — token is still fresh, no lock needed
    if _cached_token and (now - _token_acquired_at) < _TOKEN_STALE_SECS:
        return _cached_token
    # Slow path — acquire lock and re-check (another coroutine may have refreshed)
    async with _token_lock:
        now = time.monotonic()
        if _cached_token and (now - _token_acquired_at) < _TOKEN_STALE_SECS:
            return _cached_token
        cred = _get_credential()
        token = await asyncio.to_thread(cred.get_token, FABRIC_SCOPE)
        _cached_token = token.token
        _token_acquired_at = now
        logger.debug("Fabric token refreshed")
        return _cached_token
