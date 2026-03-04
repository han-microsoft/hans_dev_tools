"""Provision Fabric IQ Ontology — data-driven from graph_schema.yaml.

Module role:
    Reads vertex types, edge types, and property type hints from
    graph_schema.yaml and generates the full Fabric ontology definition
    dynamically — entity types, property IDs, relationship types, data
    bindings, and contextualizations. Zero hardcoded schema: adding new
    entity types or relationships requires only editing graph_schema.yaml
    and adding the corresponding CSV files.

Ontology generation pipeline:
    1. Parse graph_schema.yaml (vertices + edges)
    2. Allocate deterministic IDs for entity types, properties, relationships
    3. Build entity types with property mappings
    4. Group edges by (label, source, target) and build relationship types
    5. Create static data bindings (Lakehouse tables → entity types)
    6. Create contextualizations (junction table bindings → relationship types)
    7. Assemble base64-encoded definition parts array
    8. Create or update ontology via Fabric REST API
    9. Verify auto-created Graph Model item
    10. Write IDs to azure_config.env

Prerequisites:
    - provision_lakehouse.py + provision_eventhouse.py have run
    - azure_config.env populated with FABRIC_WORKSPACE_ID, etc.
    - graph_schema.yaml exists in the active scenario directory
    - Optional: property_types mapping in graph_schema.yaml for non-String types

Key collaborators:
    - ``_config.py``              — shared constants and credential helpers
    - ``graph_schema.yaml``       — declarative graph schema definition
    - Fabric REST API             — ontology CRUD + graph model auto-creation

Dependents:
    Called by: ``deploy.sh`` as the final Fabric provisioning step

Usage:
    uv run provision_ontology.py
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict

import requests
import yaml
from azure.identity import DefaultAzureCredential

from _config import (
    FABRIC_API, FABRIC_SCOPE, PROJECT_ROOT,
    WORKSPACE_ID, KQL_DB_NAME, ONTOLOGY_NAME,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCENARIO = os.environ.get("DEFAULT_SCENARIO", "")
if not SCENARIO:
    print("ERROR: DEFAULT_SCENARIO not set"); sys.exit(1)

LAKEHOUSE_ID = os.getenv("FABRIC_LAKEHOUSE_ID", "")
EVENTHOUSE_ID = os.getenv("FABRIC_EVENTHOUSE_ID", "")

_SCHEMA_PATH = PROJECT_ROOT / "data" / "scenarios" / SCENARIO / "graph_schema.yaml"
if not _SCHEMA_PATH.exists():
    print(f"ERROR: graph_schema.yaml not found: {_SCHEMA_PATH}"); sys.exit(1)

with open(_SCHEMA_PATH) as _f:
    GRAPH_SCHEMA = yaml.safe_load(_f)


def _discover_item_id(workspace_id: str, item_type: str, headers: dict) -> str:
    """Look up a Fabric item ID by type via REST API.

    Fallback discovery for when the item ID is not yet in azure_config.env.
    Lists all workspace items and returns the first matching the given type.

    Parameters:
        workspace_id: Fabric workspace GUID.
        item_type: Fabric item type string (e.g. ``Lakehouse``, ``Eventhouse``).
        headers: Auth headers from ``get_fabric_headers()`` or ``FabricClient.headers``.

    Returns:
        Item ID string, or empty string if not found or API error.
    """
    r = requests.get(f"{FABRIC_API}/workspaces/{workspace_id}/items", headers=headers)
    if r.status_code != 200:
        return ""
    for item in r.json().get("value", []):
        if item.get("type") == item_type:
            return item["id"]
    return ""

# ---------------------------------------------------------------------------
# Env file updater
# ---------------------------------------------------------------------------

def update_env_file(updates: dict[str, str]):
    """Update azure_config.env with key=value pairs (upsert semantics).

    For each key: if it exists, replaces the value in-place using regex.
    If it does not exist, appends it at the end of the file.

    Parameters:
        updates: Dict of {env_var_name: value} pairs to write.

    Side effects:
        Reads and rewrites azure_config.env in the graph_data root.
    """
    env_file = str(PROJECT_ROOT / "azure_config.env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            content = f.read()
    else:
        content = ""

    for key, value in updates.items():
        pattern = rf"^{re.escape(key)}=.*$"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, f"{key}={value}", content, flags=re.MULTILINE)
        else:
            content = content.rstrip("\n") + f"\n{key}={value}\n"

    with open(env_file, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def b64(obj: dict) -> str:
    """Base64-encode a dict as compact JSON for Fabric ontology definition parts.

    Fabric's ontology API accepts definition parts as base64-encoded JSON
    payloads. This helper serialises a dict to JSON bytes and encodes them.

    Parameters:
        obj: Dict to serialise and encode.

    Returns:
        Base64-encoded string of the JSON representation.
    """
    return base64.b64encode(json.dumps(obj).encode()).decode()


def duuid(seed: str) -> str:
    """Generate a deterministic UUID5 from a seed string.

    Uses DNS namespace for stable, reproducible IDs across runs.
    Same seed always produces the same UUID, ensuring idempotent
    ontology definitions that can be updated without ID drift.

    Parameters:
        seed: Seed string (e.g. ``CoreRouter-static``, ``connects_to-AggSwitch``).

    Returns:
        UUID5 string derived from the seed.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


def prop(pid: int, name: str, vtype: str = "String") -> dict:
    """Build an EntityTypeProperty dict for the ontology definition.

    Parameters:
        pid: Unique property ID (allocated by ``_next_prop_id()``).
        name: Property name matching the CSV column header.
        vtype: Ontology value type (``String``, ``Int64``, ``Double``, ``DateTime``).

    Returns:
        Dict conforming to the Fabric ontology EntityTypeProperty schema.
    """
    return {
        "id": str(pid),
        "name": name,
        "redefines": None,
        "baseTypeNamespaceType": None,
        "valueType": vtype,
    }


# ── Dynamic ID allocation — deterministic from declaration order ───────────
# Entity type IDs start at 1000000000001, properties at 2000000000001, and
# relationship types at 3000000000001. The large base values avoid collisions
# with Fabric's internal IDs. Sequential allocation from declaration order
# ensures identical IDs across runs (deterministic + idempotent).
_et_counter = 1000000000000
_prop_counter = 2000000000000
_rel_counter = 3000000000000


def _next_et_id() -> int:
    """Allocate the next entity type ID (sequential from 1000000000001).

    Returns:
        Next available entity type ID.

    Side effects:
        Increments module-level ``_et_counter``.
    """
    global _et_counter
    _et_counter += 1
    return _et_counter


def _next_prop_id() -> int:
    """Allocate the next property ID (sequential from 2000000000001).

    Returns:
        Next available property ID.

    Side effects:
        Increments module-level ``_prop_counter``.
    """
    global _prop_counter
    _prop_counter += 1
    return _prop_counter


def _next_rel_id() -> int:
    """Allocate the next relationship type ID (sequential from 3000000000001).

    Returns:
        Next available relationship type ID.

    Side effects:
        Increments module-level ``_rel_counter``.
    """
    global _rel_counter
    _rel_counter += 1
    return _rel_counter


# ---------------------------------------------------------------------------
# Build ontology model from graph_schema.yaml
# ---------------------------------------------------------------------------

# Lookup tables populated during entity type generation
_vertex_to_et_id: dict[str, int] = {}              # vertex label → entity type ID
_vertex_prop_ids: dict[tuple[str, str], int] = {}   # (label, prop) → property ID
_vertex_id_prop: dict[str, int] = {}                # vertex label → property ID of id_column


def _build_entity_types() -> list[dict]:
    """Generate ENTITY_TYPES from graph_schema.yaml vertices."""
    entity_types = []

    for vertex in GRAPH_SCHEMA.get("vertices", []):
        label = vertex["label"]
        et_id = _next_et_id()
        _vertex_to_et_id[label] = et_id

        id_column = vertex["id_column"]
        prop_type_hints = vertex.get("property_types", {})

        properties = []
        for prop_name in vertex["properties"]:
            pid = _next_prop_id()
            _vertex_prop_ids[(label, prop_name)] = pid
            vtype = prop_type_hints.get(prop_name, "String")
            properties.append(prop(pid, prop_name, vtype))

            if prop_name == id_column:
                _vertex_id_prop[label] = pid

        entity_types.append({
            "id": str(et_id),
            "namespace": "usertypes",
            "baseEntityTypeId": None,
            "name": label,
            "entityIdParts": [str(_vertex_id_prop[label])],
            "displayNamePropertyId": str(_vertex_id_prop[label]),
            "namespaceType": "Custom",
            "visibility": "Visible",
            "properties": properties,
            "timeseriesProperties": [],
        })

    return entity_types


def _group_edges() -> tuple[
    dict[tuple[str, str, str], list[dict]],
    dict[str, set[tuple[str, str]]],
]:
    """Group edge definitions by (label, source_label, target_label).

    Returns:
        edge_groups: { (label, src_label, tgt_label) → [edge_defs...] }
        label_pairs: { label → { (src_label, tgt_label), ... } }
    """
    edge_groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    label_pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)

    for edge in GRAPH_SCHEMA.get("edges", []):
        src_label = edge["source"]["label"]
        tgt_label = edge["target"]["label"]
        key = (edge["label"], src_label, tgt_label)
        edge_groups[key].append(edge)
        label_pairs[edge["label"]].add((src_label, tgt_label))

    return dict(edge_groups), dict(label_pairs)


# Populated during relationship type generation
_rel_type_ids: dict[tuple[str, str, str], int] = {}  # (label, src, tgt) → rel ID


def _build_relationship_types(
    edge_groups: dict[tuple[str, str, str], list[dict]],
    label_pairs: dict[str, set[tuple[str, str]]],
) -> list[dict]:
    """Generate RELATIONSHIP_TYPES from edge groups."""
    relationship_types = []

    for (label, src_label, tgt_label) in edge_groups:
        rid = _next_rel_id()
        _rel_type_ids[(label, src_label, tgt_label)] = rid

        # Disambiguate name when same label appears with multiple target types
        pairs = label_pairs[label]
        if len(pairs) == 1:
            name = label
        else:
            name = f"{label}_{tgt_label.lower()}"

        src_et_id = _vertex_to_et_id[src_label]
        tgt_et_id = _vertex_to_et_id[tgt_label]

        relationship_types.append({
            "id": str(rid),
            "namespace": "usertypes",
            "name": name,
            "namespaceType": "Custom",
            "source": {"entityTypeId": str(src_et_id)},
            "target": {"entityTypeId": str(tgt_et_id)},
        })

    return relationship_types

# ---------------------------------------------------------------------------
# Static data bindings — Lakehouse tables → entity types
# ---------------------------------------------------------------------------

def lakehouse_binding(seed: str, table: str, bindings: list[tuple[str, int]]) -> dict:
    """Build a NonTimeSeries Lakehouse data binding.

    bindings: list of (sourceColumnName, targetPropertyId) tuples
    """
    return {
        "id": duuid(seed),
        "dataBindingConfiguration": {
            "dataBindingType": "NonTimeSeries",
            "propertyBindings": [
                {"sourceColumnName": col, "targetPropertyId": str(pid)}
                for col, pid in bindings
            ],
            "sourceTableProperties": {
                "sourceType": "LakehouseTable",
                "workspaceId": WORKSPACE_ID,
                "itemId": LAKEHOUSE_ID,
                "sourceTableName": table,
            },
        },
    }


def eventhouse_binding(
    seed: str,
    table: str,
    cluster_uri: str,
    db_name: str,
    timestamp_col: str,
    bindings: list[tuple[str, int]],
) -> dict:
    """Build a TimeSeries Eventhouse data binding."""
    return {
        "id": duuid(seed),
        "dataBindingConfiguration": {
            "dataBindingType": "TimeSeries",
            "timestampColumnName": timestamp_col,
            "propertyBindings": [
                {"sourceColumnName": col, "targetPropertyId": str(pid)}
                for col, pid in bindings
            ],
            "sourceTableProperties": {
                "sourceType": "KustoTable",
                "workspaceId": WORKSPACE_ID,
                "itemId": EVENTHOUSE_ID,
                "clusterUri": cluster_uri,
                "databaseName": db_name,
                "sourceTableName": table,
            },
        },
    }


def build_static_bindings() -> dict[int, list[dict]]:
    """Generate entity_type_id → [binding, ...] from graph_schema.yaml vertices.

    Creates one Lakehouse data binding per vertex type, mapping the CSV table
    columns to the entity type's property IDs. These bindings tell the Fabric
    ontology engine where to read static data for each entity type.

    Returns:
        Dict mapping entity type ID (int) to a list of binding dicts.

    Dependencies:
        Requires ``_build_entity_types()`` to have run first (populates
        ``_vertex_to_et_id`` and ``_vertex_prop_ids`` lookup tables).
    """
    bindings: dict[int, list[dict]] = {}

    for vertex in GRAPH_SCHEMA.get("vertices", []):
        label = vertex["label"]
        et_id = _vertex_to_et_id[label]
        table_name = vertex["csv_file"].removesuffix(".csv")

        col_bindings = [
            (prop_name, _vertex_prop_ids[(label, prop_name)])
            for prop_name in vertex["properties"]
        ]

        bindings[et_id] = [
            lakehouse_binding(f"{label}-static", table_name, col_bindings)
        ]

    return bindings


# ---------------------------------------------------------------------------
# Contextualizations — bind relationship types to Lakehouse junction tables
# ---------------------------------------------------------------------------

def ctx(seed: str, table: str, src_bindings: list, tgt_bindings: list) -> dict:
    """Build a Contextualization (relationship data binding).

    A contextualization tells the ontology engine how to join a junction
    table's columns to the source and target entity type properties,
    thereby materialising a relationship from tabular data.

    Parameters:
        seed: Seed string for deterministic UUID generation.
        table: Lakehouse table name containing the junction data.
        src_bindings: List of ``(column_name, property_id)`` tuples for the source entity.
        tgt_bindings: List of ``(column_name, property_id)`` tuples for the target entity.

    Returns:
        Dict conforming to Fabric ontology Contextualization schema.
    """
    return {
        "id": duuid(seed),
        "dataBindingTable": {
            "sourceType": "LakehouseTable",
            "workspaceId": WORKSPACE_ID,
            "itemId": LAKEHOUSE_ID,
            "sourceTableName": table,
        },
        "sourceKeyRefBindings": [
            {"sourceColumnName": col, "targetPropertyId": str(pid)}
            for col, pid in src_bindings
        ],
        "targetKeyRefBindings": [
            {"sourceColumnName": col, "targetPropertyId": str(pid)}
            for col, pid in tgt_bindings
        ],
    }


def build_contextualizations(
    edge_groups: dict[tuple[str, str, str], list[dict]],
) -> dict[int, list[dict]]:
    """Generate rel_type_id → [contextualization, ...] from edge groups.

    For each relationship type (edge group), creates one or more contextualizations
    that bind the junction CSV table's source/target columns to the corresponding
    entity type property IDs.

    Parameters:
        edge_groups: Dict of ``(label, source_label, target_label) → [edge_defs]``
            as returned by ``_group_edges()``.

    Returns:
        Dict mapping relationship type ID (int) to a list of contextualization dicts.

    Dependencies:
        Requires ``_build_entity_types()`` and ``_build_relationship_types()`` to have
        run first (populates ``_vertex_prop_ids`` and ``_rel_type_ids`` lookup tables).
    """
    ctx_map: dict[int, list[dict]] = {}

    for (label, src_label, tgt_label), edges in edge_groups.items():
        rid = _rel_type_ids[(label, src_label, tgt_label)]
        ctxs = []

        for i, edge in enumerate(edges):
            table_name = edge["csv_file"].removesuffix(".csv")
            src_col = edge["source"]["column"]
            src_prop = edge["source"]["property"]
            tgt_col = edge["target"]["column"]
            tgt_prop = edge["target"]["property"]

            # Look up property IDs from the entity type definitions
            src_pid = _vertex_prop_ids[(src_label, src_prop)]
            tgt_pid = _vertex_prop_ids[(tgt_label, tgt_prop)]

            # Deterministic seed for UUID generation
            if len(edges) > 1:
                seed = f"{label}-{tgt_label}-{i}"
            else:
                seed = f"{label}-{tgt_label}"

            ctxs.append(
                ctx(seed, table_name,
                    [(src_col, src_pid)],
                    [(tgt_col, tgt_pid)])
            )

        ctx_map[rid] = ctxs

    return ctx_map


# ---------------------------------------------------------------------------
# Assemble ontology definition parts
# ---------------------------------------------------------------------------

def build_definition_parts(
    entity_types: list[dict],
    relationship_types: list[dict],
    bindings: dict[int, list[dict]],
    contextualizations: dict[int, list[dict]],
) -> list[dict]:
    """Build the full parts array for the ontology definition."""
    parts = [
        {
            "path": ".platform",
            "payload": b64({
                "metadata": {
                    "type": "Ontology",
                    "displayName": ONTOLOGY_NAME,
                },
            }),
            "payloadType": "InlineBase64",
        },
        {
            "path": "definition.json",
            "payload": b64({}),
            "payloadType": "InlineBase64",
        },
    ]

    # Entity types + data bindings
    for et in entity_types:
        et_id = et["id"]
        parts.append({
            "path": f"EntityTypes/{et_id}/definition.json",
            "payload": b64(et),
            "payloadType": "InlineBase64",
        })
        for binding in bindings.get(int(et_id), []):
            parts.append({
                "path": f"EntityTypes/{et_id}/DataBindings/{binding['id']}.json",
                "payload": b64(binding),
                "payloadType": "InlineBase64",
            })

    # Relationship types + contextualizations
    for rel in relationship_types:
        rel_id = rel["id"]
        parts.append({
            "path": f"RelationshipTypes/{rel_id}/definition.json",
            "payload": b64(rel),
            "payloadType": "InlineBase64",
        })
        for c in contextualizations.get(int(rel_id), []):
            parts.append({
                "path": f"RelationshipTypes/{rel_id}/Contextualizations/{c['id']}.json",
                "payload": b64(c),
                "payloadType": "InlineBase64",
            })

    return parts


# ---------------------------------------------------------------------------
# Fabric API client (reuses pattern from provision_lakehouse.py)
# ---------------------------------------------------------------------------

class FabricClient:
    """Thin Fabric REST API client for ontology provisioning operations.

    Purpose:
        Provides credential-managed HTTP methods for ontology CRUD, graph model
        discovery, and long-running operation polling. Follows the same pattern
        as ``FabricClient`` in ``provision_lakehouse.py``.

    Lifecycle:
        Created once in ``main()``. Credentials refreshed on each ``headers``
        access via ``DefaultAzureCredential``.
    """

    def __init__(self):
        """Initialise credential for Fabric API token acquisition."""
        self.credential = DefaultAzureCredential()

    def _token(self) -> str:
        return self.credential.get_token(FABRIC_SCOPE).token

    @property
    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        }

    def wait_for_lro(self, resp: requests.Response, label: str, timeout: int = 300):
        """Wait for a long-running operation to complete."""
        if resp.status_code == 201:
            return resp.json()
        if resp.status_code not in (200, 202):
            print(f"  ✗ {label}: {resp.status_code} — {resp.text}")
            sys.exit(1)
        if resp.status_code == 200:
            return resp.json()

        op_id = resp.headers.get("x-ms-operation-id")
        if not op_id:
            print(f"  ✗ {label}: 202 but no operation ID")
            sys.exit(1)

        retry = int(resp.headers.get("Retry-After", "5"))
        elapsed = 0
        while elapsed < timeout:
            time.sleep(retry)
            elapsed += retry
            r = requests.get(f"{FABRIC_API}/operations/{op_id}", headers=self.headers)
            if r.status_code != 200:
                continue
            status = r.json().get("status", "")
            if status == "Succeeded":
                rr = requests.get(f"{FABRIC_API}/operations/{op_id}/result", headers=self.headers)
                return rr.json() if rr.status_code == 200 else r.json()
            if status in ("Failed", "Cancelled"):
                print(f"  ✗ {label} {status}: {r.text}")
                sys.exit(1)

        print(f"  ✗ {label} timed out after {timeout}s")
        sys.exit(1)

    def find_ontology(self, workspace_id: str, name: str) -> dict | None:
        """Find an ontology by display name in the workspace.

        Parameters:
            workspace_id: Fabric workspace GUID.
            name: Ontology display name to search for.

        Returns:
            Ontology item dict if found, None otherwise.
        """
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}/ontologies",
            headers=self.headers,
        )
        r.raise_for_status()
        for item in r.json().get("value", []):
            if item["displayName"] == name:
                return item
        return None

    def delete_ontology(self, workspace_id: str, ontology_id: str, name: str):
        """Delete an Ontology by ID."""
        r = requests.delete(
            f"{FABRIC_API}/workspaces/{workspace_id}/ontologies/{ontology_id}",
            headers=self.headers,
        )
        if r.status_code in (200, 204):
            print(f"  ✓ Deleted existing Ontology: {name} ({ontology_id})")
        else:
            print(f"  ⚠ Delete Ontology failed: {r.status_code} — {r.text}")
            print(f"    Continuing anyway...")

    def create_ontology(self, workspace_id: str, name: str, parts: list[dict], max_retries: int = 10, retry_delay: int = 30) -> dict:
        """Create an Ontology, retrying on ItemDisplayNameNotAvailableYet."""
        body = {
            "displayName": name,
            "description": "Network topology ontology for autonomous NOC demo",
            "definition": {"parts": parts},
        }
        url = f"{FABRIC_API}/workspaces/{workspace_id}/ontologies"

        for attempt in range(1, max_retries + 1):
            r = requests.post(url, headers=self.headers, json=body)

            if r.status_code == 400:
                try:
                    err = r.json()
                    error_code = err.get("errorCode", "")
                except Exception:
                    error_code = ""

                if error_code == "ItemDisplayNameNotAvailableYet":
                    print(f"  ⏳ Name not available yet (attempt {attempt}/{max_retries}), retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    continue

            return self.wait_for_lro(r, f"Create Ontology '{name}'")

        print(f"  ✗ Ontology name '{name}' still not available after {max_retries} attempts ({max_retries * retry_delay}s)")
        sys.exit(1)

    def update_ontology_definition(
        self, workspace_id: str, ontology_id: str, parts: list[dict]
    ) -> dict:
        body = {"definition": {"parts": parts}}
        r = requests.post(
            f"{FABRIC_API}/workspaces/{workspace_id}/ontologies/{ontology_id}/updateDefinition",
            headers=self.headers,
            json=body,
        )
        return self.wait_for_lro(r, "Update Ontology definition")

    def get_kql_cluster_uri(self, workspace_id: str) -> str | None:
        """Get the query service URI for the first KQL database in the workspace."""
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}/kqlDatabases",
            headers=self.headers,
        )
        if r.status_code != 200:
            return None
        for db in r.json().get("value", []):
            uri = db.get("properties", {}).get("queryServiceUri", "")
            if uri:
                return uri
        return None

    def find_graph_model(self, workspace_id: str, ontology_name: str) -> dict | None:
        """Find the Graph in Microsoft Fabric child item created by an ontology.

        When an ontology is created, Fabric auto-creates a graph model item
        in the same workspace. Its displayName typically matches or contains
        the ontology name.
        """
        r = requests.get(
            f"{FABRIC_API}/workspaces/{workspace_id}/items",
            headers=self.headers,
            params={"type": "GraphModel"},
        )
        if r.status_code != 200:
            # Fallback: list all items and filter
            r = requests.get(
                f"{FABRIC_API}/workspaces/{workspace_id}/items",
                headers=self.headers,
            )
            r.raise_for_status()
        for item in r.json().get("value", []):
            # Match by type and name containing ontology name
            if item.get("type") in ("GraphModel", "Graph"):
                if ontology_name.lower() in item["displayName"].lower():
                    return item
        # Fallback: return first graph item if any
        for item in r.json().get("value", []):
            if item.get("type") in ("GraphModel", "Graph"):
                return item
        return None

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Validate config
    if not WORKSPACE_ID:
        print("✗ Missing env var: FABRIC_WORKSPACE_ID")
        print("  Run provision_lakehouse.py first and populate azure_config.env")
        sys.exit(1)

    client = FabricClient()

    # Auto-discover missing IDs via Fabric API
    global LAKEHOUSE_ID, EVENTHOUSE_ID
    if not LAKEHOUSE_ID:
        print("  ⚠ FABRIC_LAKEHOUSE_ID not set — looking up via API...")
        LAKEHOUSE_ID = _discover_item_id(WORKSPACE_ID, "Lakehouse", client.headers)
        if LAKEHOUSE_ID:
            print(f"  ✓ Discovered FABRIC_LAKEHOUSE_ID = {LAKEHOUSE_ID}")
        else:
            print("  ✗ No Lakehouse found in workspace. Run provision_lakehouse.py first.")
            sys.exit(1)
    if not EVENTHOUSE_ID:
        print("  ⚠ FABRIC_EVENTHOUSE_ID not set — looking up via API...")
        EVENTHOUSE_ID = _discover_item_id(WORKSPACE_ID, "Eventhouse", client.headers)
        if EVENTHOUSE_ID:
            print(f"  ✓ Discovered FABRIC_EVENTHOUSE_ID = {EVENTHOUSE_ID}")
        else:
            print("  ✗ No Eventhouse found in workspace. Run provision_eventhouse.py first.")
            sys.exit(1)

    print("=" * 60)
    print(f"Provisioning Fabric IQ Ontology: {ONTOLOGY_NAME}")
    print(f"  Schema source: {_SCHEMA_PATH}")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Build entity types and relationship types from graph_schema.yaml
    # ------------------------------------------------------------------
    print("\n--- Building ontology from graph_schema.yaml ---")

    entity_types = _build_entity_types()
    et_names = [et["name"] for et in entity_types]
    print(f"  ✓ {len(entity_types)} entity types: {', '.join(et_names)}")

    edge_groups, label_pairs = _group_edges()
    relationship_types = _build_relationship_types(edge_groups, label_pairs)
    rel_names = [r["name"] for r in relationship_types]
    print(f"  ✓ {len(relationship_types)} relationship types: {', '.join(rel_names)}")

    # ------------------------------------------------------------------
    # 2. Build static data bindings
    # ------------------------------------------------------------------
    bindings = build_static_bindings()
    print(f"  ✓ {sum(len(v) for v in bindings.values())} static data bindings")

    # ------------------------------------------------------------------
    # 3. Build contextualizations (relationship bindings)
    # ------------------------------------------------------------------
    contextualizations = build_contextualizations(edge_groups)
    print(f"  ✓ {sum(len(v) for v in contextualizations.values())} relationship contextualizations")

    # ------------------------------------------------------------------
    # 4. Assemble definition parts
    # ------------------------------------------------------------------
    parts = build_definition_parts(entity_types, relationship_types, bindings, contextualizations)
    print(f"  ✓ {len(parts)} definition parts total")

    # ------------------------------------------------------------------
    # 5. Create or update ontology
    # ------------------------------------------------------------------
    print(f"\n--- Creating ontology item ---")

    existing = client.find_ontology(WORKSPACE_ID, ONTOLOGY_NAME)
    if existing:
        print(f"  ⟳ Ontology already exists: {existing['id']} — deleting and recreating...")
        client.delete_ontology(WORKSPACE_ID, existing["id"], ONTOLOGY_NAME)
        time.sleep(5)  # Allow deletion to propagate

    result = client.create_ontology(WORKSPACE_ID, ONTOLOGY_NAME, parts)
    ontology_id = result.get("id", "unknown")
    print(f"  ✓ Ontology created: {ontology_id}")

    # ------------------------------------------------------------------
    # 6. Verify graph model was auto-created
    # ------------------------------------------------------------------
    # Note: GraphModel items do NOT support the Job Scheduler API.
    # The graph is automatically built/refreshed when the ontology is
    # created or its definition is updated — no separate refresh needed.
    print("\n--- Checking graph model ---")
    graph_item = client.find_graph_model(WORKSPACE_ID, ONTOLOGY_NAME)
    if graph_item:
        graph_id = graph_item["id"]
        graph_name = graph_item["displayName"]
        print(f"  ✓ Graph model exists: {graph_name} ({graph_id})")
        print(f"    Graph is auto-refreshed by ontology create/update.")
        print(f"    If data looks stale, refresh manually in Fabric portal:")
        print(f"    Workspace → {graph_name} → ... → Refresh now")
    else:
        print(f"  ⚠ Graph model not yet visible — it may take a moment to appear")
        print(f"    Check Fabric portal: Workspace → look for graph model item")

    # ------------------------------------------------------------------
    # 7. Update azure_config.env
    # ------------------------------------------------------------------
    env_updates = {
        "FABRIC_ONTOLOGY_ID": ontology_id,
    }
    if graph_item:
        env_updates["FABRIC_GRAPH_MODEL_ID"] = graph_id

    update_env_file(env_updates)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("✅ Ontology provisioning complete!")
    print(f"   Name           : {ONTOLOGY_NAME}")
    print(f"   ID             : {ontology_id}")
    print(f"   Workspace      : {WORKSPACE_ID}")
    print(f"   Entity types   : {len(entity_types)} — {', '.join(et_names)}")
    print(f"   Relationships  : {len(relationship_types)} — {', '.join(rel_names)}")
    print(f"   Schema source  : {_SCHEMA_PATH.relative_to(PROJECT_ROOT)}")
    print("=" * 60)

    print("\n  ✓ Updated azure_config.env")
    for key, value in env_updates.items():
        print(f"    {key}={value}")



if __name__ == "__main__":
    main()
