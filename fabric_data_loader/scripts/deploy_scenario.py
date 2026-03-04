"""Unified Fabric scenario deployment script.

Module role:
    Single-command entry point that orchestrates the full deployment pipeline:
    authenticate → workspace → folder → lakehouse → eventhouse → ontology → verify.

    Replaces the sequential manual invocation of ``provision_workspace.py``,
    ``provision_lakehouse.py``, ``provision_eventhouse.py``, and
    ``provision_ontology.py``. Supports cross-tenant deployments, folder-based
    organization, and YAML-based deploy manifests.

Key collaborators:
    - ``_deploy_client.FabricDeployClient`` — authenticated REST client.
    - ``_deploy_manifest.DeployManifest``   — typed configuration.
    - ``_stages/*``                         — individual deployment stages.

Usage:
    # Full deploy from CLI args
    python3 deploy_scenario.py --scenario telecom-playground \\
        --workspace-id 0a61769f-ac89-4e28-8622-71777e7029b9 \\
        --folder-path "GraphWorkshopData/telecom-playground" \\
        --tenant-id 63d18708-13a6-43b0-af5b-627b5069602c

    # Full deploy from manifest YAML
    uv run python3 deploy_scenario.py --manifest data/scenarios/telecom-playground/deploy_manifest.yaml

    # Single stage
    uv run python3 deploy_scenario.py --manifest data/scenarios/telecom-playground/deploy_manifest.yaml --stage lakehouse

    # Dry run
    uv run python3 deploy_scenario.py --manifest data/scenarios/telecom-playground/deploy_manifest.yaml --dry-run
"""

from __future__ import annotations

import argparse
import sys

from _deploy_client import DeployError, FabricDeployClient
from _deploy_manifest import DeployManifest
from _stages import auth as stage_auth
from _stages import workspace as stage_workspace
from _stages import folder as stage_folder
from _stages import lakehouse as stage_lakehouse
from _stages import eventhouse as stage_eventhouse
from _stages import ontology as stage_ontology
from _stages import verify as stage_verify


# ---------------------------------------------------------------------------
# Stage registry — maps stage names to (module, requires) tuples
# ---------------------------------------------------------------------------

STAGES = [
    ("workspace", stage_workspace),
    ("folder", stage_folder),
    ("lakehouse", stage_lakehouse),
    ("eventhouse", stage_eventhouse),
    ("ontology", stage_ontology),
    ("verify", stage_verify),
]

STAGE_NAMES = [name for name, _ in STAGES]


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        Configured ``ArgumentParser`` with all supported flags.
    """
    p = argparse.ArgumentParser(
        description="Deploy a graph scenario to Microsoft Fabric.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Full deploy with CLI args\n"
            "  python3 deploy_scenario.py --scenario telecom-playground\n"
            "\n"
            "  # Deploy from manifest\n"
            "  python3 deploy_scenario.py --manifest manifests/surya.yaml\n"
            "\n"
            "  # Single stage with force\n"
            "  python3 deploy_scenario.py --manifest m.yaml --stage lakehouse --force\n"
            "\n"
            "  # Dry run\n"
            "  python3 deploy_scenario.py --manifest m.yaml --dry-run\n"
        ),
    )

    # Manifest (overrides all other args)
    p.add_argument(
        "--manifest", metavar="FILE",
        help="Deploy manifest YAML file (overrides all other args)",
    )

    # Scenario
    p.add_argument("--scenario", metavar="NAME", help="Scenario name")

    # Workspace
    p.add_argument("--workspace-name", metavar="NAME", help="Workspace display name")
    p.add_argument("--workspace-id", metavar="ID", help="Workspace GUID (skip discovery)")
    p.add_argument("--capacity-id", metavar="ID", help="Fabric capacity GUID")

    # Folder
    p.add_argument(
        "--folder-path", metavar="PATH",
        help="Folder path (e.g. GraphWorkshopData/telecom-playground)",
    )

    # Resource names
    p.add_argument("--lakehouse-name", metavar="NAME", help="Lakehouse display name")
    p.add_argument("--eventhouse-name", metavar="NAME", help="Eventhouse display name")
    p.add_argument("--ontology-name", metavar="NAME", help="Ontology display name")

    # Auth (cross-tenant)
    p.add_argument("--tenant-id", metavar="ID", help="Target tenant ID")
    p.add_argument("--client-id", metavar="ID", help="Service principal client ID")
    p.add_argument(
        "--client-secret", metavar="SECRET",
        help="Client secret (or @env:VAR_NAME to read from env)",
    )

    # Execution control
    p.add_argument(
        "--stage",
        choices=["all"] + STAGE_NAMES,
        default="all",
        help="Run only a specific stage (default: all)",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Delete and recreate existing resources",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without making changes",
    )
    p.add_argument(
        "--output-env", metavar="FILE",
        help="Write resource IDs to env file (default: azure_config.env)",
    )

    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Parse args, build manifest and client, execute stages.

    Returns:
        Exit code: 0 on success, 1 on failure.
    """
    parser = build_parser()
    args = parser.parse_args()

    # Build manifest from YAML or CLI args
    if args.manifest:
        manifest = DeployManifest.from_yaml(args.manifest)
        # CLI overrides for manifest
        if args.force:
            manifest.force = True
        if args.dry_run:
            manifest.dry_run = True
        if args.stage != "all":
            pass  # Stage selection handled below
    else:
        if not args.scenario and not args.workspace_id:
            parser.error("Either --manifest, --scenario, or --workspace-id is required")
        manifest = DeployManifest.from_args(args)

    # Print configuration summary
    print("=" * 60)
    print("Fabric Scenario Deployment")
    print("=" * 60)
    print(f"  Scenario        : {manifest.scenario or '(none)'}")
    print(f"  Workspace       : {manifest.workspace_name or manifest.workspace_id}")
    if manifest.folder_path:
        print(f"  Folder          : {manifest.folder_path}")
    if manifest.tenant_id:
        print(f"  Target tenant   : {manifest.tenant_id}")
    if manifest.dry_run:
        print(f"  Mode            : DRY RUN")
    if manifest.force:
        print(f"  Force recreate  : YES")
    print(f"  Stage           : {args.stage}")

    # Build credential and client
    print("\n--- Stage 0: Auth ---")
    credential = stage_auth.build_credential(manifest)
    client = FabricDeployClient(credential, dry_run=manifest.dry_run)

    # Determine which stages to run
    if args.stage == "all":
        stages_to_run = STAGES
    else:
        stages_to_run = [(n, m) for n, m in STAGES if n == args.stage]

    # Execute stages sequentially
    try:
        for stage_name, stage_module in stages_to_run:
            stage_module.run(client, manifest)
    except DeployError as e:
        print(f"\n✗ Deployment failed: {e}")
        return 1
    except KeyboardInterrupt:
        print(f"\n⚠ Interrupted by user")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
