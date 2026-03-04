"""Stage 5: Ontology — generate from graph_schema.yaml and create/update.

Module role:
    Reads vertex types, edge types, and property hints from graph_schema.yaml,
    generates the full Fabric ontology definition (entity types, properties,
    relationships, data bindings, contextualizations), and either creates a
    new ontology or updates an existing one via ``updateDefinition``.

Key collaborators:
    - ``_deploy_client.FabricDeployClient`` — item CRUD, definition update.
    - ``_deploy_manifest.DeployManifest``   — paths, IDs, names.
    - ``graph_schema.yaml``                 — declarative graph schema.

Dependents:
    Verify stage reads ``manifest.ontology_id`` and ``manifest.graph_model_id``.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections import defaultdict
from pathlib import Path

import yaml

from _deploy_client import FabricDeployClient
from _deploy_manifest import DeployManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64(obj: dict) -> str:
    """Base64-encode a dict as compact JSON for ontology definition parts."""
    return base64.b64encode(json.dumps(obj).encode()).decode()


def _duuid(seed: str) -> str:
    """Generate a deterministic UUID5 from a seed string."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


def _prop(pid: int, name: str, vtype: str = "String") -> dict:
    """Build an EntityTypeProperty dict."""
    return {
        "id": str(pid),
        "name": name,
        "redefines": None,
        "baseTypeNamespaceType": None,
        "valueType": vtype,
    }


# ---------------------------------------------------------------------------
# Ontology builder
# ---------------------------------------------------------------------------

class OntologyBuilder:
    """Generates a Fabric ontology definition from graph_schema.yaml.

    Encapsulates the ID allocation, entity type generation, relationship
    type generation, static data binding, and contextualization logic.
    All IDs are deterministic (sequential from declaration order) ensuring
    identical definitions across runs.

    Parameters:
        graph_schema: Parsed graph_schema.yaml dict.
        workspace_id: Fabric workspace GUID (for data bindings).
        lakehouse_id: Lakehouse item GUID (for static entity bindings).
        eventhouse_id: Eventhouse item GUID (for timeseries bindings).
        kql_query_uri: KQL query endpoint (for timeseries bindings).
        kql_db_name: KQL database name (for timeseries bindings).
        ontology_name: Display name for the ontology item.
    """

    def __init__(
        self,
        graph_schema: dict,
        workspace_id: str,
        lakehouse_id: str,
        eventhouse_id: str,
        kql_query_uri: str,
        kql_db_name: str,
        ontology_name: str,
    ):
        self._schema = graph_schema
        self._workspace_id = workspace_id
        self._lakehouse_id = lakehouse_id
        self._eventhouse_id = eventhouse_id
        self._kql_query_uri = kql_query_uri
        self._kql_db_name = kql_db_name
        self._ontology_name = ontology_name

        # ID counters — sequential for deterministic allocation
        self._et_counter = 1000000000000
        self._prop_counter = 2000000000000
        self._rel_counter = 3000000000000

        # Lookup tables populated during generation
        self._vertex_to_et_id: dict[str, int] = {}
        self._vertex_prop_ids: dict[tuple[str, str], int] = {}
        self._vertex_id_prop: dict[str, int] = {}
        self._rel_type_ids: dict[tuple[str, str, str], int] = {}

    def _next_et_id(self) -> int:
        self._et_counter += 1
        return self._et_counter

    def _next_prop_id(self) -> int:
        self._prop_counter += 1
        return self._prop_counter

    def _next_rel_id(self) -> int:
        self._rel_counter += 1
        return self._rel_counter

    def build_entity_types(self) -> list[dict]:
        """Generate entity types from schema vertices."""
        entity_types = []
        for vertex in self._schema.get("vertices", []):
            label = vertex["label"]
            et_id = self._next_et_id()
            self._vertex_to_et_id[label] = et_id

            id_column = vertex["id_column"]
            prop_type_hints = vertex.get("property_types", {})

            properties = []
            for prop_name in vertex["properties"]:
                pid = self._next_prop_id()
                self._vertex_prop_ids[(label, prop_name)] = pid
                vtype = prop_type_hints.get(prop_name, "String")
                properties.append(_prop(pid, prop_name, vtype))
                if prop_name == id_column:
                    self._vertex_id_prop[label] = pid

            entity_types.append({
                "id": str(et_id),
                "namespace": "usertypes",
                "baseEntityTypeId": None,
                "name": label,
                "entityIdParts": [str(self._vertex_id_prop[label])],
                "displayNamePropertyId": str(self._vertex_id_prop[label]),
                "namespaceType": "Custom",
                "visibility": "Visible",
                "properties": properties,
                "timeseriesProperties": [],
            })
        return entity_types

    def build_relationship_types(self) -> tuple[list[dict], dict, dict]:
        """Generate relationship types from schema edges.

        Returns:
            Tuple of (relationship_types, edge_groups, label_pairs).
        """
        edge_groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        label_pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)

        for edge in self._schema.get("edges", []):
            src = edge["source"]["label"]
            tgt = edge["target"]["label"]
            key = (edge["label"], src, tgt)
            edge_groups[key].append(edge)
            label_pairs[edge["label"]].add((src, tgt))

        relationship_types = []
        for (label, src_label, tgt_label) in edge_groups:
            rid = self._next_rel_id()
            self._rel_type_ids[(label, src_label, tgt_label)] = rid

            pairs = label_pairs[label]
            name = label if len(pairs) == 1 else f"{label}_{tgt_label.lower()}"

            relationship_types.append({
                "id": str(rid),
                "namespace": "usertypes",
                "name": name,
                "namespaceType": "Custom",
                "source": {"entityTypeId": str(self._vertex_to_et_id[src_label])},
                "target": {"entityTypeId": str(self._vertex_to_et_id[tgt_label])},
            })

        return relationship_types, dict(edge_groups), dict(label_pairs)

    def build_static_bindings(self) -> dict[int, list[dict]]:
        """Generate Lakehouse data bindings for each entity type."""
        bindings: dict[int, list[dict]] = {}
        for vertex in self._schema.get("vertices", []):
            label = vertex["label"]
            et_id = self._vertex_to_et_id[label]
            table_name = vertex["csv_file"].removesuffix(".csv")

            col_bindings = [
                (pname, self._vertex_prop_ids[(label, pname)])
                for pname in vertex["properties"]
            ]

            binding = {
                "id": _duuid(f"{label}-static"),
                "dataBindingConfiguration": {
                    "dataBindingType": "NonTimeSeries",
                    "propertyBindings": [
                        {"sourceColumnName": col, "targetPropertyId": str(pid)}
                        for col, pid in col_bindings
                    ],
                    "sourceTableProperties": {
                        "sourceType": "LakehouseTable",
                        "workspaceId": self._workspace_id,
                        "itemId": self._lakehouse_id,
                        "sourceTableName": table_name,
                    },
                },
            }
            bindings[et_id] = [binding]
        return bindings

    def build_contextualizations(
        self, edge_groups: dict[tuple[str, str, str], list[dict]]
    ) -> dict[int, list[dict]]:
        """Generate contextualizations (relationship data bindings)."""
        ctx_map: dict[int, list[dict]] = {}

        for (label, src_label, tgt_label), edges in edge_groups.items():
            rid = self._rel_type_ids[(label, src_label, tgt_label)]
            ctxs = []

            for i, edge in enumerate(edges):
                table_name = edge["csv_file"].removesuffix(".csv")
                src_col = edge["source"]["column"]
                src_prop = edge["source"]["property"]
                tgt_col = edge["target"]["column"]
                tgt_prop = edge["target"]["property"]

                src_pid = self._vertex_prop_ids[(src_label, src_prop)]
                tgt_pid = self._vertex_prop_ids[(tgt_label, tgt_prop)]

                seed = f"{label}-{tgt_label}-{i}" if len(edges) > 1 else f"{label}-{tgt_label}"

                ctxs.append({
                    "id": _duuid(seed),
                    "dataBindingTable": {
                        "sourceType": "LakehouseTable",
                        "workspaceId": self._workspace_id,
                        "itemId": self._lakehouse_id,
                        "sourceTableName": table_name,
                    },
                    "sourceKeyRefBindings": [
                        {"sourceColumnName": src_col, "targetPropertyId": str(src_pid)}
                    ],
                    "targetKeyRefBindings": [
                        {"sourceColumnName": tgt_col, "targetPropertyId": str(tgt_pid)}
                    ],
                })

            ctx_map[rid] = ctxs

        return ctx_map

    def build_definition_parts(
        self,
        entity_types: list[dict],
        relationship_types: list[dict],
        bindings: dict[int, list[dict]],
        contextualizations: dict[int, list[dict]],
    ) -> list[dict]:
        """Assemble the full definition parts array for the ontology."""
        parts = [
            {
                "path": ".platform",
                "payload": _b64({
                    "metadata": {
                        "type": "Ontology",
                        "displayName": self._ontology_name,
                    },
                }),
                "payloadType": "InlineBase64",
            },
            {
                "path": "definition.json",
                "payload": _b64({}),
                "payloadType": "InlineBase64",
            },
        ]

        # Entity types + bindings
        for et in entity_types:
            et_id = et["id"]
            parts.append({
                "path": f"EntityTypes/{et_id}/definition.json",
                "payload": _b64(et),
                "payloadType": "InlineBase64",
            })
            for binding in bindings.get(int(et_id), []):
                parts.append({
                    "path": f"EntityTypes/{et_id}/DataBindings/{binding['id']}.json",
                    "payload": _b64(binding),
                    "payloadType": "InlineBase64",
                })

        # Relationship types + contextualizations
        for rel in relationship_types:
            rel_id = rel["id"]
            parts.append({
                "path": f"RelationshipTypes/{rel_id}/definition.json",
                "payload": _b64(rel),
                "payloadType": "InlineBase64",
            })
            for c in contextualizations.get(int(rel_id), []):
                parts.append({
                    "path": f"RelationshipTypes/{rel_id}/Contextualizations/{c['id']}.json",
                    "payload": _b64(c),
                    "payloadType": "InlineBase64",
                })

        return parts


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def run(client: FabricDeployClient, manifest: DeployManifest) -> None:
    """Execute the ontology stage: generate definition, create or update.

    Parameters:
        client: Authenticated Fabric REST client (user credential).
        manifest: Deploy manifest — ``ontology_id`` and ``graph_model_id``
            set on completion.
    """
    print("\n--- Stage 5: Ontology ---")

    if not manifest.schema_path or not Path(manifest.schema_path).exists():
        print(f"  ⚠ graph_schema.yaml not found: {manifest.schema_path}")
        print("    Skipping ontology stage")
        return

    if not manifest.lakehouse_id:
        # Auto-discover lakehouse by name when running standalone (--stage ontology)
        print("  ⚠ No lakehouse_id — discovering from workspace...")
        lh = client.find_item(manifest.workspace_id, "Lakehouse", manifest.lakehouse_name)
        if lh:
            manifest.lakehouse_id = lh["id"]
            print(f"  ✓ Found lakehouse: {manifest.lakehouse_name} ({manifest.lakehouse_id})")
        else:
            print(f"  ✗ Lakehouse '{manifest.lakehouse_name}' not found — cannot create data bindings")
            return

    if not manifest.eventhouse_id:
        # Auto-discover eventhouse too
        eh = client.find_item(manifest.workspace_id, "Eventhouse", manifest.eventhouse_name)
        if eh:
            manifest.eventhouse_id = eh["id"]
            print(f"  ✓ Found eventhouse: {manifest.eventhouse_name} ({manifest.eventhouse_id})")
            # Also discover KQL DB
            kql_db = client.find_kql_database_for_eventhouse(manifest.workspace_id, manifest.eventhouse_id)
            if kql_db:
                manifest.kql_db_name = kql_db["displayName"]
                manifest.kql_query_uri = kql_db.get("properties", {}).get("queryServiceUri", "")
                print(f"  ✓ Found KQL DB: {manifest.kql_db_name}")

    # Load graph schema
    with open(manifest.schema_path) as f:
        graph_schema = yaml.safe_load(f)

    # Build ontology definition
    builder = OntologyBuilder(
        graph_schema=graph_schema,
        workspace_id=manifest.workspace_id,
        lakehouse_id=manifest.lakehouse_id,
        eventhouse_id=manifest.eventhouse_id,
        kql_query_uri=manifest.kql_query_uri,
        kql_db_name=manifest.kql_db_name,
        ontology_name=manifest.ontology_name,
    )

    entity_types = builder.build_entity_types()
    print(f"  ✓ {len(entity_types)} entity types: {', '.join(e['name'] for e in entity_types)}")

    relationship_types, edge_groups, label_pairs = builder.build_relationship_types()
    print(f"  ✓ {len(relationship_types)} relationship types: {', '.join(r['name'] for r in relationship_types)}")

    bindings = builder.build_static_bindings()
    print(f"  ✓ {sum(len(v) for v in bindings.values())} static data bindings")

    contextualizations = builder.build_contextualizations(edge_groups)
    print(f"  ✓ {sum(len(v) for v in contextualizations.values())} contextualizations")

    parts = builder.build_definition_parts(
        entity_types, relationship_types, bindings, contextualizations
    )
    print(f"  ✓ {len(parts)} definition parts total")

    # Find existing ontology via dedicated /ontologies endpoint
    existing = client.find_ontology(manifest.workspace_id, manifest.ontology_name)

    if existing:
        # Always delete and recreate — updateDefinition is unreliable and
        # can timeout on partially-created or stale ontologies. The working
        # provision_ontology.py uses this same delete+create strategy.
        print(f"  ⟳ Deleting existing Ontology: {existing['id']}...")
        client.delete_ontology(
            manifest.workspace_id, existing["id"], manifest.ontology_name
        )
        import time
        time.sleep(5)  # Allow Fabric namespace release to propagate

    # Create fresh ontology via dedicated /ontologies endpoint
    print(f"  Creating Ontology: {manifest.ontology_name}...")
    result = client.create_ontology(
        workspace_id=manifest.workspace_id,
        name=manifest.ontology_name,
        parts=parts,
        description=f"Graph ontology for {manifest.scenario}",
    )
    manifest.ontology_id = result.get("id", "")
    print(f"  ✓ Ontology created: {manifest.ontology_id}")

    # Check for auto-created GraphModel
    print(f"\n  Checking for auto-created graph model...")
    graph_items = client.list_items(manifest.workspace_id, "GraphModel")
    for item in graph_items:
        if manifest.ontology_name.lower() in item["displayName"].lower():
            manifest.graph_model_id = item["id"]
            print(f"  ✓ Graph model: {item['displayName']} ({item['id']})")
            break
    else:
        if graph_items:
            manifest.graph_model_id = graph_items[0]["id"]
            print(f"  ✓ Graph model: {graph_items[0]['displayName']} ({graph_items[0]['id']})")
        else:
            print("  ⚠ Graph model not yet visible — may take a moment")
