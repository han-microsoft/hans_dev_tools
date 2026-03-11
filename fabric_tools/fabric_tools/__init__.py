"""Fabric Tools — Standalone query tools for Microsoft Fabric.

Exports:
  query_graph     — GQL queries against Fabric Graph Model (ontology)
  query_telemetry — KQL queries against Fabric Eventhouse
  query_alerts    — KQL queries for alert data

Usage:
  from fabric_tools import query_graph, query_telemetry, query_alerts
"""

from fabric_tools.graph._query import query_graph
from fabric_tools.graph._ontology import get_ontology
from fabric_tools.telemetry._query import query_telemetry
from fabric_tools.telemetry._alerts import query_alerts

__all__ = ["query_graph", "get_ontology", "query_telemetry", "query_alerts"]
