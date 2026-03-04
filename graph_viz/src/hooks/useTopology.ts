/**
 * @module useTopology
 *
 * Topology data hook — loads graph data from a static `topology.json`
 * file in the public directory.
 *
 * Normalises the JSON into `TopologyNode[]`, `TopologyEdge[]`, and a
 * computed `TopologyMeta` (counts, label list). Supports abort-on-refetch
 * via `AbortController` to prevent stale responses.
 *
 * @returns `{ data: { nodes, edges, meta }, loading, error, refetch }`
 *
 * @dependents
 *   Used by {@link GraphTopologyViewer} to supply data to the entire
 *   graph panel component tree.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import type { TopologyNode, TopologyEdge, TopologyMeta } from '../types/graph';

/* Re-export types for convenience — components import from this hook. */
export type { TopologyNode, TopologyEdge, TopologyMeta };

/** Internal shape of the topology state. */
interface TopologyData {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  meta: TopologyMeta | null;
}

/**
 * Hook to load graph topology data from /topology.json in the public dir.
 *
 * Fetches on mount and exposes a `refetch` callback for manual refresh.
 * No auth, no scenario store — pure static file load.
 */
export function useTopology() {
  const [data, setData] = useState<TopologyData>({ nodes: [], edges: [], meta: null });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /* AbortController ref prevents stale responses on rapid refetch. */
  const abortRef = useRef<AbortController | null>(null);

  const fetchTopology = useCallback(async () => {
    /* Abort any in-flight request before starting a new one. */
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    setLoading(true);
    setError(null);

    try {
      /* Load the static topology.json from the public directory. */
      const res = await fetch('/topology.json', { signal: ctrl.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();

      /* Accept both { topology_nodes, topology_edges } and { nodes, edges } formats. */
      const nodes: TopologyNode[] = json.topology_nodes ?? json.nodes ?? [];
      const edges: TopologyEdge[] = json.topology_edges ?? json.edges ?? [];
      /* Compute distinct labels sorted alphabetically for toolbar chips. */
      const labels = [...new Set(nodes.map((n: TopologyNode) => n.label))].sort();

      const meta: TopologyMeta = {
        node_count: nodes.length,
        edge_count: edges.length,
        labels,
      };

      setData({ nodes, edges, meta });
    } catch (err) {
      /* Silently ignore abort errors (expected on rapid refetch). */
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  /* Fetch on mount. */
  useEffect(() => {
    fetchTopology();
    return () => { abortRef.current?.abort(); };
  }, [fetchTopology]);

  return { data, loading, error, refetch: fetchTopology };
}
