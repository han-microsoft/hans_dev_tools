"""Populate Fabric resource IDs in azure_config.env via Fabric REST API discovery.

Module role:
    Discovers Microsoft Fabric resource GUIDs (workspace, lakehouse, eventhouse,
    KQL database) by querying the Fabric REST API, then writes the discovered IDs
    and connection URIs into ``azure_config.env`` so downstream provisioning and
    runtime scripts can reference them without manual lookup.

Architecture position:
    Post-provisioning bootstrap utility. Runs after ``provision_workspace.py``
    and ``provision_eventhouse.py`` have created the Fabric resources, but
    before any script that needs the resource IDs (e.g., ontology loading,
    telemetry ingestion).

Configuration source:
    Reads FABRIC_WORKSPACE_NAME from ``azure_config.env`` (via ``_config.py``).
    Writes the following keys back into the same file:
      - FABRIC_WORKSPACE_ID
      - FABRIC_LAKEHOUSE_ID
      - FABRIC_EVENTHOUSE_ID
      - FABRIC_KQL_DB_ID
      - FABRIC_KQL_DB_NAME
      - EVENTHOUSE_QUERY_URI

Key collaborators:
    - ``_config.py``          — provides FABRIC_API base URL, ENV_FILE path,
                                WORKSPACE_NAME, and ``get_fabric_headers()``
    - ``azure_config.env``    — the configuration file read and mutated
    - ``azure.identity``      — token acquisition (consumed indirectly via _config)

Dependents:
    Called by: deploy.sh (post-provisioning step), manual developer invocation.
    Consumed by: provision_ontology.py, telemetry ingestion scripts, app runtime.

Usage:
    uv run populate_fabric_config.py
"""

import os
import re
import sys

import requests
from azure.identity import DefaultAzureCredential

from _config import FABRIC_API, ENV_FILE, WORKSPACE_NAME, get_fabric_headers


def find_workspace(headers: dict, name: str) -> dict | None:
    """Locate a Fabric workspace by its display name via the Fabric REST API.

    Iterates the full list of workspaces accessible to the authenticated
    identity and returns the first exact match on ``displayName``.

    Parameters:
        headers: HTTP headers containing Authorization bearer token and
                 Content-Type. Obtained from ``get_fabric_headers()``.
        name:    Exact display name of the target workspace. Case-sensitive
                 match against the Fabric API response.

    Returns:
        The workspace dict (keys: ``id``, ``displayName``, ``type``, etc.)
        if found, or ``None`` if no workspace matches.

    Raises:
        requests.HTTPError: If the Fabric API returns a non-2xx status
            (e.g., 401 expired token, 403 insufficient permissions).

    Side effects:
        None. Read-only GET request.

    Dependencies:
        FABRIC_API — base URL for the Fabric REST API (from ``_config.py``).

    Called by: main()
    """
    r = requests.get(f"{FABRIC_API}/workspaces", headers=headers)
    r.raise_for_status()
    for ws in r.json().get("value", []):
        if ws["displayName"] == name:
            return ws
    return None


def find_items_by_type(headers: dict, workspace_id: str, item_type: str) -> list[dict]:
    """Retrieve all items of a specific type from a Fabric workspace.

    Fetches the complete item list for the workspace and filters client-side
    by ``item_type``. The Fabric REST API does not support server-side type
    filtering on the /items endpoint, so the full list is always fetched.

    Parameters:
        headers:      HTTP headers with Authorization bearer token.
        workspace_id: GUID of the Fabric workspace to query.
        item_type:    Fabric item type string to filter on. Valid values
                      include ``"Lakehouse"``, ``"Eventhouse"``,
                      ``"KQLDatabase"``, ``"Notebook"``, etc.

    Returns:
        List of item dicts matching the requested type. Each dict contains
        at minimum ``id``, ``displayName``, and ``type`` keys. Returns an
        empty list if no items of the requested type exist.

    Raises:
        requests.HTTPError: If the Fabric API returns a non-2xx status.

    Side effects:
        None. Read-only GET request.

    Dependencies:
        FABRIC_API — base URL for the Fabric REST API (from ``_config.py``).

    Called by: Not currently called in this module; available as a reusable
        utility for other scripts that import this module.
    """
    r = requests.get(f"{FABRIC_API}/workspaces/{workspace_id}/items", headers=headers)
    r.raise_for_status()
    return [i for i in r.json().get("value", []) if i.get("type") == item_type]


def get_kql_db_details(headers: dict, workspace_id: str, db_id: str) -> dict:
    """Retrieve detailed properties of a KQL database from the Fabric REST API.

    The /kqlDatabases/{id} endpoint returns extended metadata not available
    from the general /items listing — most importantly ``queryServiceUri``
    (the Kusto ingestion/query endpoint) and ``databaseName`` (which may
    differ from the item's ``displayName``).

    Parameters:
        headers:      HTTP headers with Authorization bearer token.
        workspace_id: GUID of the Fabric workspace containing the database.
        db_id:        GUID of the KQL database item.

    Returns:
        Full KQL database resource dict. The ``properties`` sub-dict contains
        ``queryServiceUri`` and ``databaseName`` among other fields.

    Raises:
        requests.HTTPError: If the database does not exist or the caller
            lacks permissions (404/403), or on any other non-2xx status.

    Side effects:
        None. Read-only GET request.

    Dependencies:
        FABRIC_API — base URL for the Fabric REST API (from ``_config.py``).

    Called by: main()
    """
    r = requests.get(
        f"{FABRIC_API}/workspaces/{workspace_id}/kqlDatabases/{db_id}",
        headers=headers,
    )
    r.raise_for_status()
    return r.json()


def update_env_file(updates: dict[str, str]):
    """Write key=value pairs into azure_config.env, preserving file structure.

    For each key in ``updates``, performs a regex substitution against the
    existing file content. If the key already exists on a line (as KEY=...),
    the value portion is replaced in-place. If the key does not exist, a new
    line is appended at the end of the file. This preserves comments, blank
    lines, and the ordering of existing entries.

    Parameters:
        updates: Mapping of environment variable names to their new string
                 values. Keys must be valid env-var names (no spaces, no ``=``).
                 Values are written verbatim (no quoting applied).

    Returns:
        None.

    Raises:
        FileNotFoundError: If ENV_FILE does not exist.
        PermissionError:   If ENV_FILE is not writable.

    Side effects:
        Overwrites ENV_FILE on disk. The file is read entirely into memory,
        modified, then written back — not atomic. Concurrent writers are not
        safe.

    Dependencies:
        ENV_FILE — absolute path to azure_config.env (from ``_config.py``).

    Called by: main()
    """
    with open(ENV_FILE, "r") as f:
        content = f.read()

    for key, value in updates.items():
        # Regex anchored to line start: captures "KEY=" prefix, replaces everything after it
        pattern = rf"^({re.escape(key)}=)(.*)$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, rf"\g<1>{value}", content, flags=re.MULTILINE)
        else:
            # Key absent from file — append a new line so the file remains parseable
            content = content.rstrip("\n") + f"\n{key}={value}\n"

    with open(ENV_FILE, "w") as f:
        f.write(content)


def main():
    """Orchestrate Fabric resource discovery and write results to azure_config.env.

    Performs the following steps in order:
      1. Authenticate via ``get_fabric_headers()`` (DefaultAzureCredential).
      2. Resolve the workspace GUID from FABRIC_WORKSPACE_NAME.
      3. List all items in the workspace and extract IDs for Lakehouse,
         Eventhouse, and KQL Database (takes the first of each type).
      4. For KQL Database, fetch extended properties to obtain the query URI
         and canonical database name.
      5. Write all discovered non-empty values into azure_config.env.

    Parameters:
        None. All configuration is read from ``_config.py`` module globals.

    Returns:
        None.

    Raises:
        SystemExit(1): If the workspace specified by WORKSPACE_NAME is not
            found in the Fabric API response.
        requests.HTTPError: If any Fabric API call returns a non-2xx status.

    Side effects:
        - Prints progress and discovered values to stdout.
        - Mutates azure_config.env on disk via ``update_env_file()``.

    Dependencies:
        get_fabric_headers() — provides authenticated HTTP headers.
        find_workspace()     — workspace name → ID resolution.
        get_kql_db_details() — KQL database extended property retrieval.
        update_env_file()    — env file mutation.
        FABRIC_API, WORKSPACE_NAME — from ``_config.py``.

    Called by: __main__ guard (CLI entry point), deploy.sh.
    """
    headers = get_fabric_headers()

    # ── Workspace resolution ─────────────────────────────────────────────
    print(f"Looking up workspace: {WORKSPACE_NAME}")
    ws = find_workspace(headers, WORKSPACE_NAME)
    if not ws:
        print(f"✗ Workspace '{WORKSPACE_NAME}' not found")
        sys.exit(1)
    workspace_id = ws["id"]
    print(f"  ✓ FABRIC_WORKSPACE_ID = {workspace_id}")

    # ── Bulk item listing ────────────────────────────────────────────────
    # Single API call to fetch all items; filtered locally per type below.
    # Avoids N+1 requests for each item type.
    r = requests.get(f"{FABRIC_API}/workspaces/{workspace_id}/items", headers=headers)
    r.raise_for_status()
    items = r.json().get("value", [])

    # ── Lakehouse discovery ──────────────────────────────────────────────
    lakehouses = [i for i in items if i.get("type") == "Lakehouse"]
    lakehouse_id = ""
    if lakehouses:
        lh = lakehouses[0]  # Convention: first Lakehouse in workspace is the primary one
        lakehouse_id = lh["id"]
        print(f"  ✓ FABRIC_LAKEHOUSE_ID = {lakehouse_id}  ({lh['displayName']})")
    else:
        print("  ⚠ No Lakehouse found")

    # ── Eventhouse discovery ─────────────────────────────────────────────
    eventhouses = [i for i in items if i.get("type") == "Eventhouse"]
    eventhouse_id = ""
    if eventhouses:
        eh = eventhouses[0]  # Convention: first Eventhouse in workspace is the primary one
        eventhouse_id = eh["id"]
        print(f"  ✓ FABRIC_EVENTHOUSE_ID = {eventhouse_id}  ({eh['displayName']})")
    else:
        print("  ⚠ No Eventhouse found")

    # ── KQL Database discovery ───────────────────────────────────────────
    kql_dbs = [i for i in items if i.get("type") == "KQLDatabase"]
    kql_db_id = ""
    kql_db_name = ""
    query_uri = ""
    if kql_dbs:
        db = kql_dbs[0]  # Convention: first KQLDatabase in workspace is the primary one
        kql_db_id = db["id"]
        print(f"  ✓ FABRIC_KQL_DB_ID = {kql_db_id}  ({db['displayName']})")

        # Extended properties endpoint provides queryServiceUri and canonical databaseName
        # that are not available from the generic /items listing
        details = get_kql_db_details(headers, workspace_id, kql_db_id)
        props = details.get("properties", {})
        query_uri = props.get("queryServiceUri", "")
        # databaseName may differ from displayName if the DB was renamed after creation
        kql_db_name = props.get("databaseName", db["displayName"])
        print(f"  ✓ FABRIC_KQL_DB_NAME = {kql_db_name}")
        print(f"  ✓ EVENTHOUSE_QUERY_URI = {query_uri}")
    else:
        print("  ⚠ No KQL Database found")

    # ── Persist discovered IDs to azure_config.env ───────────────────────
    updates = {
        "FABRIC_WORKSPACE_ID": workspace_id,
        "FABRIC_LAKEHOUSE_ID": lakehouse_id,
        "FABRIC_EVENTHOUSE_ID": eventhouse_id,
        "FABRIC_KQL_DB_ID": kql_db_id,
        "FABRIC_KQL_DB_NAME": kql_db_name,
        "EVENTHOUSE_QUERY_URI": query_uri,
    }

    # Filter out empty values to avoid overwriting existing config with blanks
    # when a resource type is absent from the workspace
    updates = {k: v for k, v in updates.items() if v}

    if updates:
        update_env_file(updates)
        print(f"\n✓ Updated {len(updates)} values in azure_config.env")
    else:
        print("\n⚠ Nothing to update")


if __name__ == "__main__":
    main()
