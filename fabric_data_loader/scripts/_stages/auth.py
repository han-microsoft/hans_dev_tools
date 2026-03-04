"""Stage 0: Build credential for Fabric API authentication.

Module role:
    Selects the appropriate ``TokenCredential`` based on the deploy manifest
    configuration: service principal for cross-tenant, AzureCliCredential
    for user-based, or DefaultAzureCredential for managed identity.

Key collaborators:
    - ``_deploy_manifest.DeployManifest`` — provides tenant/client/secret config.
    - ``deploy_scenario.py`` — calls ``build_credential()`` before any stage.

Dependents:
    ``deploy_scenario.py`` uses the returned credential to construct a
    ``FabricDeployClient``.
"""

from __future__ import annotations

import os

from azure.core.credentials import TokenCredential
from azure.identity import (
    AzureCliCredential,
    DefaultAzureCredential,
)

from _deploy_manifest import DeployManifest


def build_credential(manifest: DeployManifest) -> TokenCredential:
    """Build the appropriate Azure credential for the deployment target.

    Always uses user-level credentials (AzureCliCredential) — never SP.
    Fabric's preview APIs (Ontology, Graph Model) silently timeout or
    fail when called with service principal credentials, even with
    workspace admin rights. SP-created items also cannot be managed
    by users in the portal. All provisioning must run as a user.

    Priority:
      1. tenant_id available (manifest or env)
         → ``AzureCliCredential(tenant_id=...)`` (cross-tenant user/B2B guest)
      2. Running in Azure infrastructure (App Service / AKS)
         → ``DefaultAzureCredential`` (managed identity)
      3. None of the above
         → ``AzureCliCredential()`` (local dev, home tenant)

    Prerequisites:
        User must be logged in via ``az login`` (and ``az login --tenant <id>``
        for cross-tenant scenarios) before running the deploy script.

    Parameters:
        manifest: Deploy manifest with auth configuration.

    Returns:
        A ``TokenCredential`` instance ready for Fabric API calls.
    """
    # Resolve tenant — manifest takes priority over env var
    tenant_id = manifest.tenant_id or os.environ.get("FABRIC_TENANT_ID", "")

    # Case 1: Cross-tenant — user identity via az CLI with explicit tenant
    if tenant_id:
        print(f"  Auth: AzureCliCredential (tenant={tenant_id[:8]}...)")
        return AzureCliCredential(tenant_id=tenant_id)

    # Case 2: Running in Azure — managed identity
    if os.environ.get("WEBSITE_INSTANCE_ID") or os.environ.get("KUBERNETES_SERVICE_HOST"):
        print("  Auth: DefaultAzureCredential (managed identity)")
        return DefaultAzureCredential()

    # Case 3: Local development — az CLI default tenant
    print("  Auth: AzureCliCredential (home tenant)")
    return AzureCliCredential()
