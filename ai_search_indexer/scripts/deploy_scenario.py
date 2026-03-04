"""Deploy AI Search indexes for a scenario — manifest-driven.

Module role:
    Reads a per-scenario search manifest, loads azure_config.env for Azure
    credentials, and calls the provisioner in-process. No subprocess — env
    vars, credentials, and DefaultAzureCredential all work reliably.

Usage:
    # From graph_data/scripts/azureaisearch/
    python3 deploy_scenario.py --manifest ../../data/scenarios/airline-ops/search_manifest.yaml --upload-files
    python3 deploy_scenario.py --manifest ../../data/scenarios/airline-ops/search_manifest.yaml --dry-run

Prerequisites:
    - azure_config.env in graph_data/ root (auto-loaded)
    - Auth: DefaultAzureCredential (az login locally, managed identity deployed)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

# Resolve project root: scripts/ → 1 parent up = ai_search_indexer/
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(env_path: Path) -> None:
    """Source a KEY=VALUE env file into os.environ.

    Skips comments and blank lines. Does not override existing env vars.
    """
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = value


def main() -> int:
    """Parse manifest and invoke the search index provisioner in-process."""
    parser = argparse.ArgumentParser(
        description="Deploy AI Search indexes for a scenario.",
    )
    parser.add_argument(
        "--manifest", required=True, metavar="FILE",
        help="Path to search_manifest.yaml",
    )
    parser.add_argument(
        "--upload-files", action="store_true",
        help="Upload local knowledge docs to blob storage before indexing",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without making changes",
    )
    args = parser.parse_args()

    # Auto-load azure_config.env so users don't need to source it manually
    _load_env_file(PROJECT_ROOT / "azure_config.env")

    # Load manifest
    manifest_path = Path(args.manifest).resolve()
    if not manifest_path.exists():
        print(f"ERROR: Manifest not found: {manifest_path}")
        return 1

    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)

    scenario_name = manifest.get("scenario", {}).get("name", "")
    indexes = manifest.get("indexes", [])

    if not scenario_name:
        print("ERROR: scenario.name not set in manifest")
        return 1

    print("=" * 60)
    print(f"AI Search Deployment — {scenario_name}")
    print("=" * 60)
    print(f"  Indexes: {len(indexes)}")
    for idx in indexes:
        print(f"    - {idx['name']} ({idx.get('source', '')})")
    print()

    # Import and call provisioner in-process (co-located in scripts/)
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    from provision_search_index import _build_configs_from_manifest, run

    index_configs = _build_configs_from_manifest(manifest_path)
    run(args, index_configs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
