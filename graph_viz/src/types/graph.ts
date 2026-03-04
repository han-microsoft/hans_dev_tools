/**
 * @module graph (types)
 *
 * TypeScript type definitions for graph topology data structures.
 *
 * Matches the `topology.json` format: nodes, edges, computed meta.
 *
 * - {@link TopologyNode} — a graph vertex with a label, arbitrary
 *   properties, and optional force-graph position fields.
 * - {@link TopologyEdge} — a graph edge with source/target refs,
 *   a relationship label, and properties.
 * - {@link TopologyMeta} — computed summary (counts, distinct labels).
 */

/** Graph vertex — router, switch, server, etc. */
export interface TopologyNode {
  id: string;
  label: string;
  properties: Record<string, unknown>;
  /** Force-graph computed x position */
  x?: number;
  /** Force-graph computed y position */
  y?: number;
  /** Force-graph pinned x position */
  fx?: number;
  /** Force-graph pinned y position */
  fy?: number;
}

/** Graph edge — connection, dependency, etc. */
export interface TopologyEdge {
  id: string;
  source: string | TopologyNode;
  target: string | TopologyNode;
  label: string;
  properties: Record<string, unknown>;
}

/** Computed topology summary — node/edge counts and distinct labels. */
export interface TopologyMeta {
  node_count: number;
  edge_count: number;
  labels: string[];
}
