"""Shared configuration for Fabric provisioning and management scripts.

Module role:
    Centralises Fabric API constants, credentials, and common env-loading
    so they are not duplicated across provisioning scripts. Every Fabric
    script imports from here instead of calling ``os.getenv()`` directly.

Configuration source:
    ``azure_config.env`` in the graph_data project root, loaded via
    ``python-dotenv`` at import time. This file is populated by
    ``deploy.sh`` and updated by ``populate_fabric_config.py``.

Exports:
    FABRIC_API          — base URL for Fabric REST API
    FABRIC_SCOPE        — OAuth scope for token acquisition
    WORKSPACE_ID        — Fabric workspace GUID
    WORKSPACE_NAME      — Fabric workspace display name (required)
    CAPACITY_ID         — Fabric capacity GUID (from Bicep deployment)
    LAKEHOUSE_NAME      — Lakehouse display name (required)
    EVENTHOUSE_NAME     — Eventhouse display name (required)
    KQL_DB_NAME         — KQL database name
    ONTOLOGY_NAME       — Graph ontology model name (required)
    PROJECT_ROOT        — Resolved path to graph_data root
    DATA_DIR            — Resolved path to graph_data/data
    ENV_FILE            — Resolved path to azure_config.env
    get_fabric_headers() — returns Authorization + Content-Type headers

Key collaborators:
    - ``azure_config.env``          — the configuration file loaded on import
    - ``azure.identity``            — provides DefaultAzureCredential for token acquisition
    - All ``provision_*.py`` scripts — import constants and helpers from here

Dependents:
    Called by: provision_workspace.py, provision_lakehouse.py,
    provision_eventhouse.py, provision_ontology.py, populate_fabric_config.py

Usage:
    from _config import FABRIC_API, FABRIC_SCOPE, get_fabric_headers, PROJECT_ROOT
"""

import os
from pathlib import Path

from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

# ── Paths ────────────────────────────────────────────────────────────────────
# Resolve relative to this file: scripts/fabric/_config.py → 2 parents up = graph_data root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = str(PROJECT_ROOT / "azure_config.env")
DATA_DIR = PROJECT_ROOT / "data"

# Load azure_config.env once at import time — all scripts share the same values
load_dotenv(ENV_FILE)

# ── Fabric API constants ─────────────────────────────────────────────────────
# Base URL for all Fabric REST API calls
FABRIC_API: str = os.getenv(
    "FABRIC_API_URL", "https://api.fabric.microsoft.com/v1"
)
# OAuth scope for acquiring Fabric API tokens via DefaultAzureCredential
FABRIC_SCOPE: str = os.getenv(
    "FABRIC_SCOPE", "https://api.fabric.microsoft.com/.default"
)

# ── Resource identifiers ─────────────────────────────────────────────────────
# These are populated by deploy.sh / populate_fabric_config.py into azure_config.env.
# Required values (no defaults) trigger a clear error via _require_env().


def _require_env(name: str) -> str:
    """Return env var value or raise with a clear diagnostic message.

    Parameters:
        name: Environment variable name to look up.

    Returns:
        The variable's string value.

    Raises:
        EnvironmentError: If the variable is empty or unset, with guidance
            to set it in azure_config.env before running the script.
    """
    val = os.getenv(name)
    if not val:
        raise EnvironmentError(
            f"{name} is not set. Set it in azure_config.env before running."
        )
    return val


WORKSPACE_ID: str = os.getenv("FABRIC_WORKSPACE_ID", "")          # Set by provision_workspace.py
WORKSPACE_NAME: str = _require_env("FABRIC_WORKSPACE_NAME")       # User-defined workspace name
CAPACITY_ID: str = os.getenv("FABRIC_CAPACITY_ID", "")            # From Bicep deployment output
LAKEHOUSE_NAME: str = _require_env("FABRIC_LAKEHOUSE_NAME")       # User-defined lakehouse name
EVENTHOUSE_NAME: str = _require_env("FABRIC_EVENTHOUSE_NAME")     # User-defined eventhouse name
KQL_DB_NAME: str = os.getenv("FABRIC_KQL_DB_NAME", "")            # Discovered by populate_fabric_config.py
ONTOLOGY_NAME: str = _require_env("FABRIC_ONTOLOGY_NAME")         # User-defined ontology model name


# ── Helpers ──────────────────────────────────────────────────────────────────


def get_fabric_headers() -> dict[str, str]:
    """Return authorisation headers for Fabric REST API calls.

    Acquires a fresh token via DefaultAzureCredential on each call.
    Uses ``az login`` credentials locally and managed identity in
    hosted environments (App Service, AKS).

    Returns:
        Dict with ``Authorization`` (Bearer token) and ``Content-Type`` headers.

    Side effects:
        Makes a token acquisition call to Azure Entra ID.
    """
    credential = DefaultAzureCredential()
    token = credential.get_token(FABRIC_SCOPE).token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
