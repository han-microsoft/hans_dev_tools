/**
 * @module dagreLayout
 *
 * Dagre-based hierarchical auto-layout for React Flow graphs.
 *
 * Takes React Flow nodes and edges and computes a layered layout
 * using the dagre algorithm. Returns new nodes with computed positions.
 *
 * Layout direction is configurable: TB (top-bottom), LR (left-right),
 * BT (bottom-top), RL (right-left).
 *
 * @param nodes  — React Flow Node array (positions will be overwritten)
 * @param edges  — React Flow Edge array (used for hierarchy computation)
 * @param direction — dagre rankdir: 'TB' | 'LR' | 'BT' | 'RL'
 * @returns New nodes array with computed x/y positions
 */
import dagre from '@dagrejs/dagre';
import type { Node, Edge } from '@xyflow/react';

/** Default node dimensions for dagre spacing calculations. */
const NODE_WIDTH = 220;
const NODE_HEIGHT = 80;

/**
 * Compute a dagre hierarchical layout for the given nodes and edges.
 *
 * Dagre computes center-point positions; we offset by half the node
 * dimensions so React Flow renders them correctly (top-left origin).
 */
export function getLayoutedElements(
  nodes: Node[],
  edges: Edge[],
  direction: 'TB' | 'LR' | 'BT' | 'RL' = 'TB',
): Node[] {
  const g = new dagre.graphlib.Graph();
  /* Set graph-level options: direction, node/rank spacing. */
  g.setGraph({
    rankdir: direction,
    nodesep: 60,   /* horizontal spacing between nodes in same rank */
    ranksep: 100,  /* vertical spacing between ranks */
    marginx: 20,
    marginy: 20,
  });
  /* Required for dagre — prevents edge label issues. */
  g.setDefaultEdgeLabel(() => ({}));

  /* Register each node with its dimensions. */
  nodes.forEach((node) => {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });

  /* Register each edge for hierarchy computation. */
  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  /* Run the layout algorithm. */
  dagre.layout(g);

  /* Map dagre positions back to React Flow nodes (offset from center to top-left). */
  return nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    };
  });
}
