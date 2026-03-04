"""Stage 2: Find or create workspace folder tree.

Module role:
    Ensures the folder hierarchy specified by ``manifest.folder_path``
    exists in the workspace. Creates missing segments from left to right.
    Uses the Fabric Folders API (Preview).

Key collaborators:
    - ``_deploy_client.FabricDeployClient`` — folder CRUD.
    - ``_deploy_manifest.DeployManifest``   — config + state.

Dependents:
    Lakehouse, Eventhouse, and Ontology stages use ``manifest.folder_id``
    to place items in the correct folder.
"""

from __future__ import annotations

from _deploy_client import FabricDeployClient
from _deploy_manifest import DeployManifest


def run(client: FabricDeployClient, manifest: DeployManifest) -> None:
    """Execute the folder stage: find or create nested folder path.

    Algorithm:
      1. Split ``folder_path`` by ``/`` into segments.
      2. List all folders in the workspace (recursive).
      3. For each segment, find existing folder matching (name, parent).
      4. If missing, create the folder and proceed.
      5. Set ``manifest.folder_id`` to the leaf folder's GUID.

    Parameters:
        client: Authenticated Fabric REST client.
        manifest: Deploy manifest — ``folder_id`` is set on completion.

    Side effects:
        Creates folders that don't exist. Mutates ``manifest.folder_id``.
    """
    print("\n--- Stage 2: Folder ---")

    # Skip if no folder path specified — items go to workspace root
    if not manifest.folder_path:
        print("  No folder path specified — items will be at workspace root")
        return

    segments = [s.strip() for s in manifest.folder_path.split("/") if s.strip()]
    if not segments:
        print("  Empty folder path — items will be at workspace root")
        return

    # Fetch all existing folders in one call (recursive=True is default)
    all_folders = client.list_folders(manifest.workspace_id)
    if not all_folders and len(segments) > 0:
        print("  ⚠ Folders API returned empty — API may be unavailable (Preview)")
        print("    Attempting to create folders anyway...")

    # Build a lookup: (parentFolderId, displayName) → folder dict
    # parentFolderId is None for root-level folders
    folder_lookup: dict[tuple[str | None, str], dict] = {}
    for f in all_folders:
        parent = f.get("parentFolderId")
        folder_lookup[(parent, f["displayName"])] = f

    # Walk the path segments, creating any missing folders
    parent_id: str | None = None
    for i, segment in enumerate(segments):
        existing = folder_lookup.get((parent_id, segment))
        if existing:
            parent_id = existing["id"]
            print(f"  ✓ Found folder: {segment} ({parent_id})")
        else:
            # Create the missing folder
            print(f"  Creating folder: {segment}...")
            created = client.create_folder(
                manifest.workspace_id, segment, parent_id
            )
            parent_id = created["id"]
            print(f"  ✓ Created folder: {segment} ({parent_id})")
            # Add to lookup so nested segments can find their parent
            folder_lookup[(created.get("parentFolderId"), segment)] = created

    # Set the leaf folder ID on the manifest
    manifest.folder_id = parent_id or ""
    print(f"  ✓ Target folder ID: {manifest.folder_id}")
