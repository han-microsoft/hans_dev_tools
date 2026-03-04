"""
Provision (find or create) a Microsoft Fabric workspace and persist its ID
to ``azure_config.env`` for consumption by downstream provisioning scripts.

Module role
-----------
First step in the Fabric provisioning pipeline.  Guarantees a workspace
exists and is API-accessible before any dependent resources (lakehouse,
eventhouse, ontology, RBAC policies) are created.

Key collaborators
-----------------
- ``_config`` — supplies ``FABRIC_API``, ``WORKSPACE_NAME``, ``CAPACITY_ID``,
  ``ENV_FILE``, and ``get_fabric_headers()`` (OAuth token via
  ``DefaultAzureCredential``).
- ``provision_lakehouse.py`` — immediate downstream consumer; reads
  ``FABRIC_WORKSPACE_ID`` produced here.
- ``populate_fabric_config.py`` — post-provisioning bootstrap; also depends
  on the workspace ID written by this module.

Dependents
----------
- ``deploy.sh`` (line ~625): invokes this script as the first Fabric step.
- All other ``scripts/fabric/provision_*.py`` scripts depend on the
  ``FABRIC_WORKSPACE_ID`` value this module writes to ``azure_config.env``.

Configuration (environment variables)
--------------------------------------
===============================  =========  =====================================
Variable                         Required   Description
===============================  =========  =====================================
``FABRIC_WORKSPACE_NAME``        yes        Display name for the workspace
``FABRIC_CAPACITY_ID``           no         Fabric capacity GUID (Bicep output)
``FABRIC_API_URL``               no         Override for Fabric REST endpoint
===============================  =========  =====================================

Usage::

    source azure_config.env && uv run python scripts/fabric/provision_workspace.py
"""

import os
import re
import sys
import time

import requests
from azure.identity import DefaultAzureCredential

from _config import (
    FABRIC_API,
    WORKSPACE_NAME,
    CAPACITY_ID,
    ENV_FILE,
    get_fabric_headers,
)


def find_workspace(headers: dict, name: str) -> dict | None:
    """Query the Fabric REST API for a workspace matching *name* exactly.

    Iterates the full ``/workspaces`` listing and returns the first object
    whose ``displayName`` equals *name*.  The Fabric API does not support
    server-side filtering by name, so client-side iteration is required.

    Parameters
    ----------
    headers : dict
        HTTP headers including ``Authorization: Bearer <token>`` and
        ``Content-Type: application/json``.
    name : str
        Case-sensitive display name to match against workspace objects.

    Returns
    -------
    dict | None
        Workspace JSON object (keys: ``id``, ``displayName``, ``capacityId``,
        …) if found; ``None`` otherwise.

    Side effects
    ------------
    None.

    Raises
    ------
    requests.HTTPError
        If the GET ``/workspaces`` call returns a non-2xx status.

    Dependencies
    ------------
    - ``_config.FABRIC_API`` for the base URL.
    - ``requests.get`` for the HTTP call.

    Called by: ``main()``, ``wait_for_workspace()``.
    """
    r = requests.get(f"{FABRIC_API}/workspaces", headers=headers)
    r.raise_for_status()
    for ws in r.json().get("value", []):
        if ws["displayName"] == name:
            return ws
    return None


def create_workspace(headers: dict, name: str, capacity_id: str = "") -> dict:
    """Create a new Fabric workspace via POST, optionally attaching a capacity.

    Sends a ``POST /workspaces`` request.  If the API returns 409 (name
    conflict), the function returns ``None`` to signal the caller to retry
    discovery — this handles Fabric's eventual-consistency window where a
    workspace exists but is not yet visible via GET.

    Parameters
    ----------
    headers : dict
        HTTP headers including ``Authorization`` and ``Content-Type``.
    name : str
        Display name for the new workspace.
    capacity_id : str, optional
        Fabric capacity GUID to attach at creation time.  When empty the
        workspace is created without dedicated capacity (shared/trial).

    Returns
    -------
    dict | None
        Workspace JSON on 201 Created; ``None`` on 409 Conflict (caller
        should poll via ``wait_for_workspace``).  For any other successful
        status, returns the parsed response body.

    Side effects
    ------------
    - Creates a workspace in the Fabric tenant.
    - Prints a warning to stdout on 409 conflict.

    Raises
    ------
    requests.HTTPError
        For any non-201/409 error status.

    Dependencies
    ------------
    - ``_config.FABRIC_API`` for the base URL.
    - ``requests.post`` for the HTTP call.

    Called by: ``main()``.
    """
    body = {"displayName": name}
    # Attach capacity at creation time to avoid a separate assign call
    if capacity_id:
        body["capacityId"] = capacity_id
    r = requests.post(f"{FABRIC_API}/workspaces", headers=headers, json=body)
    if r.status_code == 201:
        return r.json()
    # Handle name conflict (workspace exists but wasn't found — eventual consistency)
    if r.status_code == 409:
        print(f"  ⚠ Workspace '{name}' already exists (409 conflict) — searching again...")
        return None
    r.raise_for_status()
    return r.json()


def assign_capacity(headers: dict, workspace_id: str, capacity_id: str):
    """Attach a Fabric capacity to an existing workspace via POST.

    Calls ``POST /workspaces/{id}/assignToCapacity``.  The operation is
    idempotent: a 409 response indicates the capacity is already bound and
    is treated as success.

    Parameters
    ----------
    headers : dict
        HTTP headers including ``Authorization`` and ``Content-Type``.
    workspace_id : str
        GUID of the target workspace.
    capacity_id : str
        GUID of the Fabric capacity to assign.

    Returns
    -------
    None

    Side effects
    ------------
    - Binds the specified capacity to the workspace in Fabric.
    - Prints status to stdout (success, already-assigned, or warning).

    Raises
    ------
    No exceptions raised directly; non-success/non-409 responses are logged
    as warnings to stdout so the provisioning pipeline can continue.

    Dependencies
    ------------
    - ``_config.FABRIC_API`` for the base URL.
    - ``requests.post`` for the HTTP call.

    Called by: ``main()``.
    """
    r = requests.post(
        f"{FABRIC_API}/workspaces/{workspace_id}/assignToCapacity",
        headers=headers,
        json={"capacityId": capacity_id},
    )
    # 200/202 = assignment accepted or completed
    if r.status_code in (200, 202):
        print(f"  ✓ Capacity assigned: {capacity_id}")
    # 409 = capacity already bound — treat as idempotent success
    elif r.status_code == 409:
        print(f"  ✓ Capacity already assigned")
    else:
        print(f"  ⚠ Assign capacity: {r.status_code} — {r.text}")


def wait_for_workspace(headers: dict, name: str, max_retries: int = 10, retry_delay: int = 10) -> dict:
    """Poll the Fabric API until *name* resolves to a workspace object.

    Fabric workspace creation is eventually consistent — a successful POST
    may return before the workspace is discoverable via GET.  This function
    compensates by retrying ``find_workspace`` with a linear back-off.

    Parameters
    ----------
    headers : dict
        HTTP headers including ``Authorization`` and ``Content-Type``.
    name : str
        Display name of the workspace to wait for.
    max_retries : int, optional
        Maximum number of polling attempts (default 10 → ~100 s ceiling).
    retry_delay : int, optional
        Seconds between consecutive polls (default 10).

    Returns
    -------
    dict
        Workspace JSON object once it becomes visible.

    Side effects
    ------------
    - Sleeps up to ``max_retries * retry_delay`` seconds.
    - Prints progress to stdout on each retry.
    - Calls ``sys.exit(1)`` if the workspace never appears, terminating the
      provisioning pipeline.

    Raises
    ------
    SystemExit
        If the workspace is not found within the retry budget.
    requests.HTTPError
        Propagated from ``find_workspace`` on transient API errors.

    Dependencies
    ------------
    - ``find_workspace()`` for each probe.
    - ``time.sleep`` for inter-poll delay.

    Called by: ``main()``.
    """
    for attempt in range(1, max_retries + 1):
        ws = find_workspace(headers, name)
        if ws:
            return ws
        print(f"  ⏳ Workspace not yet visible (attempt {attempt}/{max_retries}), retrying in {retry_delay}s...")
        time.sleep(retry_delay)
    # Exhausted all retries — hard-fail to prevent downstream scripts from
    # running against a non-existent workspace
    print(f"  ✗ Workspace '{name}' not found after {max_retries} attempts")
    sys.exit(1)


def update_env_file(key: str, value: str):
    """Append or replace a ``key=value`` pair in ``azure_config.env``.

    Performs an idempotent upsert: if *key* already exists on any line the
    value is overwritten in-place; otherwise the pair is appended.  If the
    file does not exist it is created with a single entry.

    The regex match is anchored to the start of each line (``^key=…$``) so
    substrings of other keys are never accidentally replaced.

    Parameters
    ----------
    key : str
        Environment variable name (e.g. ``FABRIC_WORKSPACE_ID``).
    value : str
        Value to assign.  Must not contain newlines.

    Returns
    -------
    None

    Side effects
    ------------
    - Creates or overwrites ``azure_config.env`` on disk (path from
      ``_config.ENV_FILE``).

    Raises
    ------
    OSError
        If the file cannot be read or written (permissions, disk full, etc.).

    Dependencies
    ------------
    - ``_config.ENV_FILE`` for the target path.
    - ``os``, ``re`` standard library modules.

    Called by: ``main()``.
    """
    # Bootstrap case: env file does not yet exist, create with single entry
    if not os.path.exists(ENV_FILE):
        with open(ENV_FILE, "w") as f:
            f.write(f"{key}={value}\n")
        return

    with open(ENV_FILE, "r") as f:
        content = f.read()

    # Anchor pattern to line start so FABRIC_WORKSPACE_ID doesn't match
    # e.g. OLD_FABRIC_WORKSPACE_ID
    pattern = rf"^({re.escape(key)}=)(.*)$"
    if re.search(pattern, content, re.MULTILINE):
        # Key exists — overwrite value in-place, preserving surrounding lines
        content = re.sub(pattern, rf"\g<1>{value}", content, flags=re.MULTILINE)
    else:
        # Key absent — append after a newline to avoid concatenation with
        # the last line of the file
        content = content.rstrip("\n") + f"\n{key}={value}\n"

    with open(ENV_FILE, "w") as f:
        f.write(content)


def main():
    """Orchestrate the four-step workspace provisioning pipeline.

    Steps:
      1. Search for an existing workspace by ``FABRIC_WORKSPACE_NAME``.
      2. If absent, create the workspace (with optional capacity attachment).
      3. Ensure a Fabric capacity is bound when ``FABRIC_CAPACITY_ID`` is set.
      4. Persist ``FABRIC_WORKSPACE_ID`` to ``azure_config.env``.

    Parameters
    ----------
    None.  All configuration is read from ``_config`` module constants and
    environment variables loaded via ``dotenv``.

    Returns
    -------
    None

    Side effects
    ------------
    - Creates a Fabric workspace if one does not already exist.
    - Binds a capacity to the workspace (idempotent).
    - Writes ``FABRIC_WORKSPACE_ID`` to ``azure_config.env`` on disk.
    - Prints structured progress output to stdout.
    - May call ``sys.exit(1)`` (via ``wait_for_workspace``) on timeout.

    Raises
    ------
    requests.HTTPError
        Propagated from Fabric API calls on unrecoverable HTTP errors.
    SystemExit
        If the workspace cannot be confirmed within the retry window.

    Dependencies
    ------------
    - ``get_fabric_headers()`` — acquires an OAuth2 bearer token.
    - ``find_workspace()``, ``create_workspace()``, ``assign_capacity()``,
      ``wait_for_workspace()``, ``update_env_file()`` — called in sequence.

    Called by: ``deploy.sh`` (fabric provisioning stage), manual invocation.
    """
    # Acquire OAuth2 headers (DefaultAzureCredential → bearer token)
    headers = get_fabric_headers()

    print("=" * 60)
    print(f"Provisioning Fabric workspace: {WORKSPACE_NAME}")
    print("=" * 60)

    # --- Step 1: Idempotency check — avoid recreating an existing workspace ---
    print(f"  Looking for workspace '{WORKSPACE_NAME}'...")
    ws = find_workspace(headers, WORKSPACE_NAME)

    if ws:
        print(f"  ✓ Workspace already exists: {ws['id']}")
    else:
        # --- Step 2: Workspace absent — create and handle eventual consistency ---
        print(f"  Workspace not found — creating...")
        ws = create_workspace(headers, WORKSPACE_NAME, CAPACITY_ID)

        if ws is None:
            # 409 conflict: Fabric accepted a prior create that hasn't
            # propagated to the GET listing yet — poll until visible
            ws = wait_for_workspace(headers, WORKSPACE_NAME)
            print(f"  ✓ Found workspace after conflict: {ws['id']}")
        else:
            print(f"  ✓ Workspace created: {ws['id']}")
            # Even after 201, the workspace may not be query-visible
            # immediately — poll to guarantee downstream scripts succeed
            print(f"  Waiting for workspace to be fully accessible...")
            ws = wait_for_workspace(headers, WORKSPACE_NAME)

    workspace_id = ws["id"]

    # --- Step 3: Capacity binding (idempotent, skipped if already set) ---
    if CAPACITY_ID and not ws.get("capacityId"):
        print(f"  Assigning capacity...")
        assign_capacity(headers, workspace_id, CAPACITY_ID)

    # --- Step 4: Persist workspace ID so downstream scripts can source it ---
    update_env_file("FABRIC_WORKSPACE_ID", workspace_id)
    print(f"\n  ✓ FABRIC_WORKSPACE_ID={workspace_id} written to azure_config.env")

    print("=" * 60)
    print(f"✅ Workspace ready: {WORKSPACE_NAME} ({workspace_id})")
    print("=" * 60)


if __name__ == "__main__":
    main()
