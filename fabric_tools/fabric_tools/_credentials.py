"""Centralized Azure credential factory — single source for all Azure auth.

Module role:
    Provides ``get_azure_credential()`` which returns the best available
    Azure credential based on the runtime environment. Eliminates the
    duplicated env-sniffing logic that was copy-pasted across 6 files.

Credential priority (3-tier):
    1. ``FABRIC_TENANT_ID`` + ``FABRIC_CLIENT_ID`` + ``FABRIC_CLIENT_SECRET``
       all set → ``ClientSecretCredential`` targeting the external tenant.
       Used for cross-tenant deployments.
    2. Running in Azure (``WEBSITE_INSTANCE_ID`` or ``KUBERNETES_SERVICE_HOST``)
       → ``DefaultAzureCredential`` (managed identity).
    3. Local development → ``AzureCliCredential`` (``az login``).

    When ``require_fabric_sp=False``, tier 1 is skipped — services that
    don't need cross-tenant access (AI Foundry, Cosmos) use tiers 2-3 only.

Key collaborators:
    - ``tools/_fabric_auth.py``  — uses for Fabric token acquisition
    - ``tools/telemetry/_fabric.py`` — uses for KustoClient credential
    - ``app/services/llm/agent.py`` — uses for AI Foundry client
    - ``app/routers/service_health.py`` — uses for health checks
    - ``app/routers/models.py`` — uses for deployment listing

Dependents:
    Called by: any module that needs an Azure TokenCredential
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Module-level cache — one credential per configuration. In the async
# single-thread model, module-level caching is safe.
_cached_credential = None
_cached_credential_key: tuple[bool, str] = (False, "")


def get_azure_credential(*, require_fabric_sp: bool = False):
    """Return the best available Azure credential for the current environment.

    Args:
        require_fabric_sp: When True, checks for cross-tenant Fabric service
            principal env vars first (tier 1). When False, skips tier 1 and
            returns DefaultAzureCredential or AzureCliCredential only.

    Returns:
        A ``TokenCredential`` instance suitable for Azure SDK calls.

    Side effects:
        Caches the credential at module level on first call per configuration.
        Logs the selected credential type.
    """
    global _cached_credential, _cached_credential_key

    # Cache key includes require_fabric_sp so we don't return SP creds when
    # the caller doesn't want them, and vice versa
    cache_key = (require_fabric_sp, os.environ.get("FABRIC_TENANT_ID", ""))
    if _cached_credential is not None and _cached_credential_key == cache_key:
        return _cached_credential

    # Lazy import — avoids import-time failures if azure-identity is missing
    from azure.identity import AzureCliCredential, ClientSecretCredential, DefaultAzureCredential

    credential = None

    # Tier 1: Cross-tenant service principal (only when explicitly requested)
    if require_fabric_sp:
        tenant_id = os.environ.get("FABRIC_TENANT_ID", "")
        client_id = os.environ.get("FABRIC_CLIENT_ID", "")
        client_secret = os.environ.get("FABRIC_CLIENT_SECRET", "")
        if tenant_id and client_id and client_secret:
            logger.debug(
                "credentials: ClientSecretCredential (cross-tenant SP)",
            )
            credential = ClientSecretCredential(tenant_id, client_id, client_secret)

    # Tier 2: Managed identity in Azure (App Service / AKS / Container Apps)
    if credential is None:
        if os.environ.get("WEBSITE_INSTANCE_ID") or os.environ.get("KUBERNETES_SERVICE_HOST"):
            logger.info("credentials: DefaultAzureCredential (managed identity)")
            credential = DefaultAzureCredential()
        else:
            # Tier 3: Local development via az login
            logger.info("credentials: AzureCliCredential (local dev)")
            credential = AzureCliCredential()

    _cached_credential = credential
    _cached_credential_key = cache_key
    return credential
