"""Deploy manifest — typed configuration for unified Fabric deployment.

Module role:
    Defines the ``DeployManifest`` dataclass that replaces the fragile
    ``azure_config.env`` chain with a single, version-controllable YAML
    manifest. Supports loading from YAML files or construction from CLI args.

Key collaborators:
    - ``deploy_scenario.py``  — builds a manifest from CLI args or YAML
    - ``_stages/*.py``        — each stage reads its config from the manifest

Dependents:
    All ``_stages/`` modules and ``deploy_scenario.py``.

Usage:
    from _deploy_manifest import DeployManifest
    manifest = DeployManifest.from_yaml("deploy_manifest.yaml")
    # or
    manifest = DeployManifest.from_args(args)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class DeployManifest:
    """Typed configuration for a Fabric deployment run.

    All fields needed by every stage are collected here. Fields with defaults
    are optional; the deploy pipeline fills in discovered values (e.g.
    workspace_id, lakehouse_id) as it progresses through stages.

    Attributes:
        scenario: Scenario name (e.g. ``telecom-playground``).
        data_root: Absolute path to the scenario's data directory.
        schema_path: Absolute path to ``graph_schema.yaml``.
        scenario_yaml_path: Absolute path to ``scenario.yaml``.
        tenant_id: Target tenant ID for cross-tenant deploys. Empty = home tenant.
        client_id: Service principal client ID. Empty = user auth.
        client_secret: SP client secret. Empty = user auth.
        workspace_name: Target workspace display name.
        workspace_id: Workspace GUID. Discovered if empty.
        capacity_id: Fabric capacity GUID to bind.
        folder_path: Slash-separated folder path (e.g. ``GraphWorkshopData/telecom``).
        folder_id: Leaf folder GUID. Discovered by folder stage.
        lakehouse_name: Display name for the Lakehouse item.
        lakehouse_id: Lakehouse GUID. Set by lakehouse stage.
        eventhouse_name: Display name for the Eventhouse item.
        eventhouse_id: Eventhouse GUID. Set by eventhouse stage.
        kql_db_name: KQL database name. Discovered from Eventhouse.
        kql_query_uri: KQL query endpoint URI. Discovered from Eventhouse.
        ontology_name: Display name for the Ontology item.
        ontology_id: Ontology GUID. Set by ontology stage.
        graph_model_id: Auto-created GraphModel GUID. Set by ontology stage.
        force: Delete and recreate existing resources.
        dry_run: Print actions without executing.
        output_env_file: Path to write resource IDs back to an env file.
        onelake_dfs_endpoint: OneLake DFS endpoint. Discovered from workspace.
    """

    # ── Scenario ─────────────────────────────────────────────────────────
    scenario: str = ""
    data_root: str = ""
    schema_path: str = ""
    scenario_yaml_path: str = ""

    # ── Auth / target ────────────────────────────────────────────────────
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""

    # ── Workspace ────────────────────────────────────────────────────────
    workspace_name: str = ""
    workspace_id: str = ""
    capacity_id: str = ""

    # ── Folder ───────────────────────────────────────────────────────────
    folder_path: str = ""
    folder_id: str = ""

    # ── Lakehouse ────────────────────────────────────────────────────────
    lakehouse_name: str = ""
    lakehouse_id: str = ""

    # ── Eventhouse ───────────────────────────────────────────────────────
    eventhouse_name: str = ""
    eventhouse_id: str = ""
    kql_db_name: str = ""
    kql_query_uri: str = ""

    # ── Ontology ─────────────────────────────────────────────────────────
    ontology_name: str = ""
    ontology_id: str = ""
    graph_model_id: str = ""

    # ── Options ──────────────────────────────────────────────────────────
    force: bool = False
    dry_run: bool = False
    output_env_file: str = ""
    onelake_dfs_endpoint: str = ""

    # ── Resolved paths (computed, not serialized) ────────────────────────
    _project_root: Path = field(default_factory=lambda: Path.cwd(), repr=False)

    @classmethod
    def from_yaml(cls, path: str) -> DeployManifest:
        """Load a deploy manifest from a YAML file.

        YAML structure:
            target:
              tenant_id, client_id, client_secret_env, workspace_name,
              workspace_id, capacity_id, folder_path
            scenario:
              name, data_root
            resources:
              lakehouse: {name}
              eventhouse: {name}
              ontology: {name, schema}
            output:
              env_file

        Parameters:
            path: Path to the YAML manifest file.

        Returns:
            Populated ``DeployManifest`` instance.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            yaml.YAMLError: On malformed YAML.
        """
        with open(path) as f:
            cfg = yaml.safe_load(f)

        # Resolve the project root relative to this file (scripts/fabric/)
        project_root = Path(__file__).resolve().parent.parent.parent

        target = cfg.get("target", {})
        scenario_cfg = cfg.get("scenario", {})
        resources = cfg.get("resources", {})
        output = cfg.get("output", {})

        scenario_name = scenario_cfg.get("name", "")
        data_root_rel = scenario_cfg.get(
            "data_root", f"data/scenarios/{scenario_name}"
        )
        data_root = str(project_root / data_root_rel)

        schema_rel = resources.get("ontology", {}).get("schema", "graph_schema.yaml")
        schema_path = str(Path(data_root) / schema_rel)
        scenario_yaml = str(Path(data_root) / "scenario.yaml")

        # Resolve client secret from env var indirection
        secret_env = target.get("client_secret_env", "")
        client_secret = os.environ.get(secret_env, "") if secret_env else ""

        return cls(
            scenario=scenario_name,
            data_root=data_root,
            schema_path=schema_path,
            scenario_yaml_path=scenario_yaml,
            tenant_id=target.get("tenant_id", ""),
            client_id=target.get("client_id", ""),
            client_secret=client_secret,
            workspace_name=target.get("workspace_name", ""),
            workspace_id=target.get("workspace_id", ""),
            capacity_id=target.get("capacity_id", ""),
            folder_path=target.get("folder_path", ""),
            lakehouse_name=resources.get("lakehouse", {}).get("name", ""),
            eventhouse_name=resources.get("eventhouse", {}).get("name", ""),
            ontology_name=resources.get("ontology", {}).get("name", ""),
            output_env_file=output.get("env_file", ""),
            force=cfg.get("force", False),
            dry_run=cfg.get("dry_run", False),
            _project_root=project_root,
        )

    @classmethod
    def from_args(cls, args) -> DeployManifest:
        """Build a manifest from CLI argparse namespace.

        Parameters:
            args: ``argparse.Namespace`` with fields matching the CLI definition.

        Returns:
            Populated ``DeployManifest`` instance.
        """
        project_root = Path(__file__).resolve().parent.parent.parent
        scenario = getattr(args, "scenario", "")

        # Default data paths from scenario name
        data_root = str(project_root / "data" / "scenarios" / scenario) if scenario else ""
        schema_path = str(Path(data_root) / "graph_schema.yaml") if data_root else ""
        scenario_yaml = str(Path(data_root) / "scenario.yaml") if data_root else ""

        # Resolve @env:VAR_NAME secret references
        secret = getattr(args, "client_secret", "") or ""
        if secret.startswith("@env:"):
            secret = os.environ.get(secret[5:], "")

        # Default resource names from scenario name
        scenario_slug = scenario.replace("-", "_").title().replace("_", "") if scenario else ""

        return cls(
            scenario=scenario,
            data_root=data_root,
            schema_path=schema_path,
            scenario_yaml_path=scenario_yaml,
            tenant_id=getattr(args, "tenant_id", "") or "",
            client_id=getattr(args, "client_id", "") or "",
            client_secret=secret,
            workspace_name=getattr(args, "workspace_name", "") or "",
            workspace_id=getattr(args, "workspace_id", "") or "",
            capacity_id=getattr(args, "capacity_id", "") or "",
            folder_path=getattr(args, "folder_path", "") or "",
            lakehouse_name=getattr(args, "lakehouse_name", "") or f"LH_{scenario_slug}",
            eventhouse_name=getattr(args, "eventhouse_name", "") or f"EH_{scenario_slug}",
            ontology_name=getattr(args, "ontology_name", "") or f"ONT_{scenario_slug}",
            force=getattr(args, "force", False),
            dry_run=getattr(args, "dry_run", False),
            output_env_file=getattr(args, "output_env", "") or "",
            _project_root=project_root,
        )

    @property
    def project_root(self) -> Path:
        """Absolute path to the graph_data project root."""
        return self._project_root

    @property
    def entity_csv_dir(self) -> str:
        """Absolute path to the entity CSV directory."""
        return str(Path(self.data_root) / "data" / "entities")

    @property
    def telemetry_csv_dir(self) -> str:
        """Absolute path to the telemetry CSV directory."""
        return str(Path(self.data_root) / "data" / "telemetry")
