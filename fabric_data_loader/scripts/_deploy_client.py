"""Authenticated Fabric REST API client for unified deployment.

Module role:
    Provides a single ``FabricDeployClient`` class that encapsulates all
    Fabric REST API interactions needed by the deploy pipeline: workspace
    CRUD, folder CRUD, generic item CRUD, LRO polling, and token management.
    Replaces the duplicated ``FabricClient`` classes scattered across
    ``provision_lakehouse.py``, ``provision_eventhouse.py``, and
    ``provision_ontology.py``.

Key collaborators:
    - ``_stages/*.py``        — each stage module receives a client instance
    - ``deploy_scenario.py``  — creates the client and passes it to stages
    - ``_deploy_manifest.py`` — provides config consumed by the client

Dependents:
    All ``_stages/`` modules and ``deploy_scenario.py``.

Usage:
    from _deploy_client import FabricDeployClient
    client = FabricDeployClient(credential)
"""

from __future__ import annotations

import sys
import time
from typing import Any

import requests
from azure.core.credentials import TokenCredential


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Base URL for all Fabric REST API v1 calls
FABRIC_API = "https://api.fabric.microsoft.com/v1"

# OAuth scope for Fabric API token acquisition
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"

# Error codes returned when a recently-deleted item's display name has not
# yet been released from the Fabric namespace. Retries are appropriate for
# all of these codes.
_NAME_CONFLICT_CODES = frozenset({
    "ItemDisplayNameNotAvailableYet",
    "DatamartCreationFailedDueToBadRequest",
    "ItemDisplayNameAlreadyInUse",
})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DeployError(Exception):
    """Raised when a Fabric deployment operation fails non-recoverably."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class FabricDeployClient:
    """Authenticated REST client for all Fabric deployment operations.

    Purpose:
        Centralises credential management, HTTP header construction, and
        long-running operation (LRO) polling into a single reusable class.
        Each deployment stage receives an instance and calls the appropriate
        methods without worrying about auth or polling mechanics.

    Role in system:
        Data-access / infrastructure layer. Sits below the ``_stages/``
        orchestration layer and above the raw ``requests`` HTTP library.

    Lifecycle:
        Created once in ``deploy_scenario.py.main()``, passed to all stages.
        The ``TokenCredential`` instance handles token caching internally.

    Key collaborators:
        - ``azure.identity`` credential classes — token providers.
        - ``requests`` — HTTP transport.
        - Fabric REST API v1 — workspace, folder, item, LRO endpoints.
    """

    def __init__(self, credential: TokenCredential, dry_run: bool = False):
        """Initialise with a pre-built credential.

        Parameters:
            credential: Any ``azure.core.credentials.TokenCredential`` — e.g.
                ``AzureCliCredential``, ``ClientSecretCredential``, or
                ``DefaultAzureCredential``.
            dry_run: When True, mutating calls (POST/DELETE/PATCH) print what
                they would do instead of executing.
        """
        self.credential = credential
        self.dry_run = dry_run

    # ── Token / headers ──────────────────────────────────────────────────

    def _get_token(self) -> str:
        """Acquire a bearer token scoped to the Fabric API.

        Returns:
            Raw JWT access token string.

        Raises:
            azure.core.exceptions.ClientAuthenticationError: If the
            credential chain cannot produce a valid token.
        """
        return self.credential.get_token(FABRIC_SCOPE).token

    @property
    def headers(self) -> dict[str, str]:
        """Build HTTP headers with a fresh bearer token.

        Returns:
            Dict with ``Authorization`` and ``Content-Type`` headers.
        """
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    # ── LRO polling ──────────────────────────────────────────────────────

    def poll_lro(
        self, response: requests.Response, label: str, timeout: int = 300
    ) -> dict[str, Any]:
        """Poll a Fabric long-running operation until completion.

        Handles HTTP 200 (immediate sync success), 201 (created), and 202
        (accepted — poll required). Any other status raises ``DeployError``.

        Parameters:
            response: Initial HTTP response from the Fabric API call.
            label: Human-readable description for log/error messages.
            timeout: Maximum seconds to wait before aborting.

        Returns:
            Parsed JSON body of the completed operation result.

        Raises:
            DeployError: On non-recoverable failures (unexpected status,
                missing operation ID, operation failure/cancellation, timeout).
        """
        # Synchronous success — resource created immediately
        if response.status_code in (200, 201):
            try:
                return response.json()
            except ValueError:
                return {}

        # Anything other than 202 is unexpected
        if response.status_code != 202:
            raise DeployError(
                f"{label} failed: HTTP {response.status_code} — {response.text}"
            )

        # 202 Accepted — extract operation ID for polling
        operation_id = response.headers.get("x-ms-operation-id")
        if not operation_id:
            raise DeployError(f"{label}: 202 response with no operation ID")

        url = f"{FABRIC_API}/operations/{operation_id}"
        # Respect the server's suggested polling interval to avoid throttling
        retry_after = int(response.headers.get("Retry-After", "5"))

        elapsed = 0
        while elapsed < timeout:
            time.sleep(retry_after)
            elapsed += retry_after
            r = requests.get(url, headers=self.headers)
            # Transient poll failures are silently retried
            if r.status_code != 200:
                continue
            status = r.json().get("status", "")
            if status == "Succeeded":
                # Fetch the created resource from /operations/{id}/result
                result_url = f"{url}/result"
                rr = requests.get(result_url, headers=self.headers)
                if rr.status_code == 200:
                    return rr.json()
                return r.json()
            if status in ("Failed", "Cancelled"):
                raise DeployError(f"{label} {status}: {r.text}")

        raise DeployError(f"{label} timed out after {timeout}s")

    # ── Workspace operations ─────────────────────────────────────────────

    def list_workspaces(self) -> list[dict]:
        """List all workspaces accessible to the authenticated identity.

        Returns:
            List of workspace dicts (keys: ``id``, ``displayName``, etc.).

        Raises:
            requests.HTTPError: On non-2xx status.
        """
        r = requests.get(f"{FABRIC_API}/workspaces", headers=self.headers)
        r.raise_for_status()
        return r.json().get("value", [])

    def find_workspace(self, name: str) -> dict | None:
        """Find a workspace by exact display name.

        Parameters:
            name: Case-sensitive workspace display name.

        Returns:
            Workspace dict if found, None otherwise.
        """
        for ws in self.list_workspaces():
            if ws["displayName"] == name:
                return ws
        return None

    def get_workspace(self, workspace_id: str) -> dict:
        """Get workspace details by ID.

        Parameters:
            workspace_id: Fabric workspace GUID.

        Returns:
            Workspace dict with ``id``, ``displayName``, ``oneLakeEndpoints``, etc.

        Raises:
            requests.HTTPError: On non-2xx status.
        """
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}", headers=self.headers
        )
        r.raise_for_status()
        return r.json()

    def create_workspace(self, name: str, capacity_id: str = "") -> dict | None:
        """Create a new workspace, optionally attaching a capacity.

        Parameters:
            name: Display name for the workspace.
            capacity_id: Optional Fabric capacity GUID to bind at creation.

        Returns:
            Workspace dict on 201 Created; None on 409 Conflict (caller
            should poll via find_workspace).

        Raises:
            requests.HTTPError: On non-201/409 errors.
        """
        if self.dry_run:
            print(f"  [DRY RUN] Would create workspace: {name}")
            return {"id": "dry-run-workspace-id", "displayName": name}

        body: dict[str, Any] = {"displayName": name}
        if capacity_id:
            body["capacityId"] = capacity_id
        r = requests.post(f"{FABRIC_API}/workspaces", headers=self.headers, json=body)
        if r.status_code == 201:
            return r.json()
        if r.status_code == 409:
            return None
        r.raise_for_status()
        return r.json()

    def assign_capacity(self, workspace_id: str, capacity_id: str) -> None:
        """Bind a Fabric capacity to a workspace (idempotent).

        Parameters:
            workspace_id: Workspace GUID.
            capacity_id: Capacity GUID to assign.
        """
        if self.dry_run:
            print(f"  [DRY RUN] Would assign capacity {capacity_id}")
            return

        r = requests.post(
            f"{FABRIC_API}/workspaces/{workspace_id}/assignToCapacity",
            headers=self.headers,
            json={"capacityId": capacity_id},
        )
        if r.status_code not in (200, 202, 409):
            print(f"  ⚠ Assign capacity: {r.status_code} — {r.text}")

    # ── Folder operations (Preview API) ──────────────────────────────────

    def list_folders(
        self, workspace_id: str, root_folder_id: str | None = None
    ) -> list[dict]:
        """List folders in a workspace, optionally under a specific root.

        Parameters:
            workspace_id: Workspace GUID.
            root_folder_id: Optional parent folder ID to scope the listing.

        Returns:
            List of folder dicts (keys: ``id``, ``displayName``,
            ``parentFolderId``, ``workspaceId``).
        """
        params: dict[str, str] = {}
        if root_folder_id:
            params["rootFolderId"] = root_folder_id
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}/folders",
            headers=self.headers,
            params=params,
        )
        if r.status_code != 200:
            # Folders API is Preview — degrade gracefully if unavailable
            return []
        return r.json().get("value", [])

    def create_folder(
        self, workspace_id: str, name: str, parent_folder_id: str | None = None
    ) -> dict:
        """Create a folder in a workspace.

        Parameters:
            workspace_id: Workspace GUID.
            name: Folder display name.
            parent_folder_id: Optional parent folder GUID. None = workspace root.

        Returns:
            Created folder dict with ``id``, ``displayName``, etc.

        Raises:
            DeployError: On failure.
        """
        if self.dry_run:
            print(f"  [DRY RUN] Would create folder: {name}")
            return {"id": "dry-run-folder-id", "displayName": name}

        body: dict[str, Any] = {"displayName": name}
        if parent_folder_id:
            body["parentFolderId"] = parent_folder_id
        r = requests.post(
            f"{FABRIC_API}/workspaces/{workspace_id}/folders",
            headers=self.headers,
            json=body,
        )
        if r.status_code in (200, 201):
            return r.json()
        raise DeployError(
            f"Create folder '{name}' failed: {r.status_code} — {r.text}"
        )

    # ── Generic item operations ──────────────────────────────────────────

    def list_items(
        self, workspace_id: str, item_type: str | None = None
    ) -> list[dict]:
        """List items in a workspace, optionally filtered by type.

        Parameters:
            workspace_id: Workspace GUID.
            item_type: Optional Fabric item type to filter (client-side).

        Returns:
            List of item dicts.
        """
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}/items", headers=self.headers
        )
        r.raise_for_status()
        items = r.json().get("value", [])
        if item_type:
            items = [i for i in items if i.get("type") == item_type]
        return items

    def find_item(
        self, workspace_id: str, item_type: str, name: str
    ) -> dict | None:
        """Find an item by type and display name.

        Parameters:
            workspace_id: Workspace GUID.
            item_type: Fabric item type (e.g. ``Lakehouse``, ``Eventhouse``).
            name: Case-sensitive display name.

        Returns:
            Item dict if found, None otherwise.
        """
        for item in self.list_items(workspace_id, item_type):
            if item["displayName"] == name:
                return item
        return None

    def create_item(
        self,
        workspace_id: str,
        item_type: str,
        name: str,
        folder_id: str | None = None,
        description: str = "",
        definition: dict | None = None,
        creation_payload: dict | None = None,
        max_retries: int = 12,
        retry_delay: int = 30,
    ) -> dict:
        """Create a Fabric item with retry logic for name availability.

        Parameters:
            workspace_id: Workspace GUID.
            item_type: Fabric item type (``Lakehouse``, ``Eventhouse``, ``Ontology``, etc.).
            name: Display name for the item.
            folder_id: Optional folder GUID to place the item in.
            description: Optional item description.
            definition: Optional item definition (for Ontology, etc.).
            creation_payload: Optional creation payload (alternative to definition).
            max_retries: Retry count for name-conflict errors.
            retry_delay: Seconds between retries.

        Returns:
            Created item dict.

        Raises:
            DeployError: If name never becomes available or LRO fails.
        """
        if self.dry_run:
            print(f"  [DRY RUN] Would create {item_type}: {name}")
            return {"id": "dry-run-item-id", "type": item_type, "displayName": name}

        body: dict[str, Any] = {"displayName": name, "type": item_type}
        if folder_id:
            body["folderId"] = folder_id
        if description:
            body["description"] = description
        if definition:
            body["definition"] = definition
        if creation_payload:
            body["creationPayload"] = creation_payload

        url = f"{FABRIC_API}/workspaces/{workspace_id}/items"

        for attempt in range(1, max_retries + 1):
            r = requests.post(url, headers=self.headers, json=body)

            # Check for retryable name-conflict errors
            if r.status_code == 400:
                try:
                    err = r.json()
                    error_code = err.get("errorCode", "")
                    error_msg = err.get("message", "").lower()
                except Exception:
                    error_code, error_msg = "", ""

                name_held = (
                    error_code in _NAME_CONFLICT_CODES
                    or "name is already in use" in error_msg
                    or "name not available" in error_msg
                )
                if name_held:
                    print(
                        f"  ⏳ Name not released yet "
                        f"(attempt {attempt}/{max_retries}), "
                        f"retrying in {retry_delay}s..."
                    )
                    time.sleep(retry_delay)
                    continue

            return self.poll_lro(r, f"Create {item_type} '{name}'")

        raise DeployError(
            f"{item_type} name '{name}' not available after "
            f"{max_retries} attempts ({max_retries * retry_delay}s)"
        )

    def delete_item(self, workspace_id: str, item_id: str, label: str = "") -> bool:
        """Delete a Fabric item by ID.

        Parameters:
            workspace_id: Workspace GUID.
            item_id: Item GUID.
            label: Human-readable label for log messages.

        Returns:
            True on success (200/204), False on failure.
        """
        if self.dry_run:
            print(f"  [DRY RUN] Would delete {label or item_id}")
            return True

        r = requests.delete(
            f"{FABRIC_API}/workspaces/{workspace_id}/items/{item_id}",
            headers=self.headers,
        )
        if r.status_code in (200, 204):
            print(f"  ✓ Deleted: {label or item_id}")
            return True
        print(f"  ⚠ Delete {label or item_id} failed: {r.status_code} — {r.text}")
        return False

    def update_item_definition(
        self, workspace_id: str, item_id: str, definition: dict, label: str = ""
    ) -> dict:
        """Update an existing item's definition (e.g. Ontology).

        Parameters:
            workspace_id: Workspace GUID.
            item_id: Item GUID.
            definition: New definition dict (``{"parts": [...]}``).
            label: Human-readable label for log messages.

        Returns:
            Operation result dict.

        Raises:
            DeployError: On LRO failure.
        """
        if self.dry_run:
            print(f"  [DRY RUN] Would update definition: {label or item_id}")
            return {}

        r = requests.post(
            f"{FABRIC_API}/workspaces/{workspace_id}/items/{item_id}/updateDefinition",
            headers=self.headers,
            json={"definition": definition},
        )
        return self.poll_lro(r, f"Update definition '{label or item_id}'")

    # ── Typed item sub-endpoints ─────────────────────────────────────────

    def list_lakehouses(self, workspace_id: str) -> list[dict]:
        """List lakehouses in a workspace via the typed endpoint."""
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}/lakehouses",
            headers=self.headers,
        )
        r.raise_for_status()
        return r.json().get("value", [])

    def load_lakehouse_table(
        self,
        workspace_id: str,
        lakehouse_id: str,
        table_name: str,
        relative_path: str,
    ) -> None:
        """Load a CSV from lakehouse Files/ into a managed delta table.

        Parameters:
            workspace_id: Workspace GUID.
            lakehouse_id: Lakehouse item GUID.
            table_name: Target delta table name.
            relative_path: Path relative to the lakehouse root (e.g. ``Files/Foo.csv``).

        Raises:
            DeployError: On LRO failure.
        """
        if self.dry_run:
            print(f"  [DRY RUN] Would load table: {table_name}")
            return

        body = {
            "relativePath": relative_path,
            "pathType": "File",
            "mode": "Overwrite",
            "formatOptions": {"format": "Csv", "header": True, "delimiter": ","},
        }
        r = requests.post(
            f"{FABRIC_API}/workspaces/{workspace_id}"
            f"/lakehouses/{lakehouse_id}/tables/{table_name}/load",
            headers=self.headers,
            json=body,
        )
        self.poll_lro(r, f"Load table '{table_name}'")

    def list_kql_databases(self, workspace_id: str) -> list[dict]:
        """List KQL databases in a workspace."""
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}/kqlDatabases",
            headers=self.headers,
        )
        r.raise_for_status()
        return r.json().get("value", [])

    def find_kql_database_for_eventhouse(
        self, workspace_id: str, eventhouse_id: str
    ) -> dict | None:
        """Find the KQL database auto-created with an Eventhouse.

        Parameters:
            workspace_id: Workspace GUID.
            eventhouse_id: Parent Eventhouse item GUID.

        Returns:
            KQL database dict if found, None otherwise. Falls back to
            the first KQL database if parent matching fails.
        """
        dbs = self.list_kql_databases(workspace_id)
        # Prefer exact match on parent Eventhouse
        for db in dbs:
            if db.get("properties", {}).get("parentEventhouseItemId") == eventhouse_id:
                return db
        # Fallback to first available DB
        return dbs[0] if dbs else None

    # ── Ontology-specific endpoints ──────────────────────────────────

    def create_ontology(
        self,
        workspace_id: str,
        name: str,
        parts: list[dict],
        description: str = "",
        max_retries: int = 12,
        retry_delay: int = 30,
    ) -> dict:
        """Create an ontology via the dedicated /ontologies endpoint.

        Uses ``POST /v1/workspaces/{id}/ontologies`` instead of the generic
        ``/items`` endpoint. The ontology API accepts the definition directly
        in the request body.

        Parameters:
            workspace_id: Workspace GUID.
            name: Ontology display name.
            parts: Definition parts array (base64-encoded JSON payloads).
            description: Optional description.
            max_retries: Retry count for name-conflict errors.
            retry_delay: Seconds between retries.

        Returns:
            Created ontology dict.

        Raises:
            DeployError: If name never becomes available or LRO fails.
        """
        if self.dry_run:
            print(f"  [DRY RUN] Would create Ontology: {name}")
            return {"id": "dry-run-ontology-id", "displayName": name}

        body: dict[str, Any] = {
            "displayName": name,
            "definition": {"parts": parts},
        }
        if description:
            body["description"] = description

        url = f"{FABRIC_API}/workspaces/{workspace_id}/ontologies"

        for attempt in range(1, max_retries + 1):
            r = requests.post(url, headers=self.headers, json=body)

            if r.status_code == 400:
                try:
                    err = r.json()
                    error_code = err.get("errorCode", "")
                    error_msg = err.get("message", "").lower()
                except Exception:
                    error_code, error_msg = "", ""

                name_held = (
                    error_code in _NAME_CONFLICT_CODES
                    or "name is already in use" in error_msg
                    or "name not available" in error_msg
                )
                if name_held:
                    print(
                        f"  ⏳ Name not released yet "
                        f"(attempt {attempt}/{max_retries}), "
                        f"retrying in {retry_delay}s..."
                    )
                    time.sleep(retry_delay)
                    continue

            return self.poll_lro(r, f"Create Ontology '{name}'", timeout=600)

        raise DeployError(
            f"Ontology name '{name}' not available after "
            f"{max_retries} attempts ({max_retries * retry_delay}s)"
        )

    def update_ontology_definition(
        self,
        workspace_id: str,
        ontology_id: str,
        parts: list[dict],
        label: str = "",
    ) -> dict:
        """Update an existing ontology's definition via the dedicated endpoint.

        Uses ``POST /v1/workspaces/{id}/ontologies/{ontologyId}/updateDefinition``.

        Parameters:
            workspace_id: Workspace GUID.
            ontology_id: Ontology item GUID.
            parts: New definition parts array.
            label: Human-readable label for logs.

        Returns:
            Operation result dict.

        Raises:
            DeployError: On LRO failure.
        """
        if self.dry_run:
            print(f"  [DRY RUN] Would update Ontology definition: {label or ontology_id}")
            return {}

        r = requests.post(
            f"{FABRIC_API}/workspaces/{workspace_id}/ontologies/{ontology_id}/updateDefinition",
            headers=self.headers,
            json={"definition": {"parts": parts}},
        )
        return self.poll_lro(r, f"Update Ontology '{label or ontology_id}'", timeout=600)

    def find_ontology(self, workspace_id: str, name: str) -> dict | None:
        """Find an ontology by display name via the dedicated /ontologies endpoint.

        Parameters:
            workspace_id: Workspace GUID.
            name: Ontology display name to search for.

        Returns:
            Ontology dict if found, None otherwise.
        """
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}/ontologies",
            headers=self.headers,
        )
        if r.status_code != 200:
            return None
        for item in r.json().get("value", []):
            if item["displayName"] == name:
                return item
        return None

    def delete_ontology(self, workspace_id: str, ontology_id: str, label: str = "") -> bool:
        """Delete an ontology via the dedicated /ontologies endpoint.

        Parameters:
            workspace_id: Workspace GUID.
            ontology_id: Ontology item GUID.
            label: Human-readable label for logs.

        Returns:
            True on success.
        """
        if self.dry_run:
            print(f"  [DRY RUN] Would delete Ontology: {label or ontology_id}")
            return True

        r = requests.delete(
            f"{FABRIC_API}/workspaces/{workspace_id}/ontologies/{ontology_id}",
            headers=self.headers,
        )
        if r.status_code in (200, 204):
            print(f"  ✓ Deleted Ontology: {label or ontology_id}")
            return True
        print(f"  ⚠ Delete Ontology failed: {r.status_code} — {r.text[:200]}")
        return False
