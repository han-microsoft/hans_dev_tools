"""Stage 1: Find or create Fabric workspace.

Module role:
    Ensures a Fabric workspace exists and is capacity-bound. Discovers
    workspace ID and OneLake endpoints, mutating the manifest for
    downstream stages.

Key collaborators:
    - ``_deploy_client.FabricDeployClient`` — REST operations.
    - ``_deploy_manifest.DeployManifest``   — config + state.

Dependents:
    All subsequent stages require ``manifest.workspace_id``.
"""

from __future__ import annotations

import time

from _deploy_client import FabricDeployClient
from _deploy_manifest import DeployManifest


def run(client: FabricDeployClient, manifest: DeployManifest) -> None:
    """Execute the workspace stage: find or create, bind capacity.

    Parameters:
        client: Authenticated Fabric REST client.
        manifest: Deploy manifest — ``workspace_id`` is set on completion.

    Side effects:
        - Creates a workspace if none exists with the given name.
        - Assigns capacity if ``capacity_id`` is set and not yet bound.
        - Mutates ``manifest.workspace_id`` and ``manifest.onelake_dfs_endpoint``.
    """
    print("\n--- Stage 1: Workspace ---")

    # If workspace_id is already provided, just validate it exists
    if manifest.workspace_id:
        ws = client.get_workspace(manifest.workspace_id)
        print(f"  ✓ Using existing workspace: {ws['displayName']} ({ws['id']})")
        # Capture OneLake endpoint for cross-tenant file uploads
        endpoints = ws.get("oneLakeEndpoints", {})
        manifest.onelake_dfs_endpoint = endpoints.get("dfsEndpoint", "")
        return

    # Search by name
    if not manifest.workspace_name:
        raise ValueError("Either --workspace-id or --workspace-name is required")

    ws = client.find_workspace(manifest.workspace_name)

    if ws:
        print(f"  ✓ Found workspace: {ws['displayName']} ({ws['id']})")
    else:
        # Create workspace with optional capacity binding
        print(f"  Creating workspace: {manifest.workspace_name}...")
        ws = client.create_workspace(manifest.workspace_name, manifest.capacity_id)

        if ws is None:
            # 409 conflict — workspace exists but not yet visible; poll
            print("  ⏳ Workspace created (409 conflict) — waiting for visibility...")
            for attempt in range(10):
                time.sleep(10)
                ws = client.find_workspace(manifest.workspace_name)
                if ws:
                    break
                print(f"  ⏳ Not yet visible (attempt {attempt + 1}/10)...")
            if not ws:
                raise RuntimeError(
                    f"Workspace '{manifest.workspace_name}' not found after 10 attempts"
                )

        print(f"  ✓ Workspace ready: {ws['id']}")

    manifest.workspace_id = ws["id"]

    # Bind capacity if specified and not already bound
    if manifest.capacity_id and not ws.get("capacityId"):
        print(f"  Assigning capacity {manifest.capacity_id[:8]}...")
        client.assign_capacity(manifest.workspace_id, manifest.capacity_id)
        print("  ✓ Capacity assigned")

    # Capture OneLake DFS endpoint for cross-tenant uploads
    endpoints = ws.get("oneLakeEndpoints", {})
    manifest.onelake_dfs_endpoint = endpoints.get("dfsEndpoint", "")
    if not manifest.onelake_dfs_endpoint:
        # Fetch full workspace details to get endpoints
        ws_full = client.get_workspace(manifest.workspace_id)
        endpoints = ws_full.get("oneLakeEndpoints", {})
        manifest.onelake_dfs_endpoint = endpoints.get("dfsEndpoint", "")
