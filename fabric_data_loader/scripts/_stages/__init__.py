"""Deployment stages for unified Fabric scenario provisioning.

Package role:
    Each module in this package implements one stage of the deployment
    pipeline. Stages are independent, composable functions that accept
    a ``FabricDeployClient`` and a ``DeployManifest``, perform their
    work, and mutate the manifest with discovered resource IDs.

Stage execution order:
    auth → workspace → folder → lakehouse → eventhouse → ontology → verify
"""
