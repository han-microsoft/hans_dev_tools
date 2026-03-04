/**
 * @module ReactFlowTopologyViewer
 *
 * React Flow backend for the graph topology visualizer.
 *
 * Takes the same topology.json data as the force-graph backend and
 * renders it using React Flow with dagre hierarchical auto-layout.
 *
 * Features:
 *   - Rich card-style nodes (TopologyNode) with status indicators
 *   - Animated custom edges (TopologyEdge) with label badges
 *   - Dagre auto-layout with switchable direction (TB/LR/BT/RL)
 *   - Built-in React Flow controls: MiniMap, Controls, Background
 *   - Node type filtering (same filter bar as force-graph)
 *   - Zoom-to-fit on load
 *
 * @props
 *   - `width`  — available pixel width
 *   - `height` — available pixel height
 */
import { useState, useEffect, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeTypes,
  type EdgeTypes,
  MarkerType,
  useReactFlow,
  ReactFlowProvider,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useTopology, type TopologyNode, type TopologyEdge } from '@/hooks/useTopology';
import { useNodeColor } from '@/hooks/useNodeColor';
import { TopologyNodeComponent } from './TopologyNode';
import { TopologyEdgeComponent } from './TopologyEdge';
import { getLayoutedElements } from './dagreLayout';
import { COLOR_PALETTE } from '@/constants/graphConstants';

/** Register custom node types with React Flow. */
const nodeTypes: NodeTypes = {
  topology: TopologyNodeComponent,
};

/** Register custom edge types with React Flow. */
const edgeTypes: EdgeTypes = {
  topology: TopologyEdgeComponent,
};

/** Available layout directions. */
const DIRECTIONS = [
  { key: 'TB', label: '↓ Top-Down' },
  { key: 'LR', label: '→ Left-Right' },
  { key: 'BT', label: '↑ Bottom-Up' },
  { key: 'RL', label: '← Right-Left' },
] as const;

type LayoutDirection = 'TB' | 'LR' | 'BT' | 'RL';

interface ReactFlowTopologyViewerProps {
  width: number;
  height: number;
}

/** Deterministic label colour — matches force-graph backend. */
function labelColor(label: string): string {
  let hash = 0;
  for (const ch of label) hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0;
  return COLOR_PALETTE[Math.abs(hash) % COLOR_PALETTE.length];
}

/**
 * Inner component — uses the useReactFlow hook which requires
 * being inside ReactFlowProvider. Handles data conversion,
 * layout, filtering, and the toolbar.
 */
function ReactFlowInner({ width, height }: ReactFlowTopologyViewerProps) {
  const { data, loading, error, refetch } = useTopology();
  const { fitView } = useReactFlow();

  const [direction, setDirection] = useState<LayoutDirection>('TB');
  const [activeLabels, setActiveLabels] = useState<string[]>([]);
  const [nodeColorOverride] = useState<Record<string, string>>({});
  const getColor = useNodeColor(nodeColorOverride);

  /* Filter nodes by active label set. */
  const filteredNodes = useMemo(() => {
    if (activeLabels.length === 0) return data.nodes;
    return data.nodes.filter((n) => activeLabels.includes(n.label));
  }, [data.nodes, activeLabels]);

  /* Filter edges — both endpoints must be in the visible node set. */
  const filteredEdges = useMemo(() => {
    const nodeIdSet = new Set(filteredNodes.map((n) => n.id));
    return data.edges.filter((e) => {
      const srcId = typeof e.source === 'string' ? e.source : e.source.id;
      const tgtId = typeof e.target === 'string' ? e.target : e.target.id;
      return nodeIdSet.has(srcId) && nodeIdSet.has(tgtId);
    });
  }, [data.edges, filteredNodes]);

  /* Convert topology data to React Flow format. */
  const { rfNodes, rfEdges } = useMemo(() => {
    const rfNodes: Node[] = filteredNodes.map((node: TopologyNode) => ({
      id: node.id,
      type: 'topology',
      position: { x: 0, y: 0 }, /* dagre will compute real positions */
      data: {
        nodeId: node.id,
        label: node.label,
        properties: node.properties,
      },
    }));

    const rfEdges: Edge[] = filteredEdges.map((edge: TopologyEdge) => ({
      id: edge.id,
      source: typeof edge.source === 'string' ? edge.source : edge.source.id,
      target: typeof edge.target === 'string' ? edge.target : edge.target.id,
      type: 'topology',
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
      data: { label: edge.label, properties: edge.properties },
    }));

    return { rfNodes, rfEdges };
  }, [filteredNodes, filteredEdges]);

  /* Apply dagre layout. */
  const layoutedNodes = useMemo(
    () => getLayoutedElements(rfNodes, rfEdges, direction),
    [rfNodes, rfEdges, direction],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(rfEdges);

  /* Re-apply layout when data or direction changes. */
  useEffect(() => {
    setNodes(layoutedNodes);
    setEdges(rfEdges);
    /* Fit view after a short delay so React Flow finishes rendering. */
    setTimeout(() => fitView({ padding: 0.15, duration: 400 }), 100);
  }, [layoutedNodes, rfEdges, setNodes, setEdges, fitView]);

  /* Available node labels for the filter bar. */
  const availableLabels = data.meta?.labels ?? [];

  return (
    <div className="h-full flex flex-col overflow-hidden border border-border bg-neutral-bg1">
      {/* Header bar — mirrors force-graph header style */}
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border shrink-0">
        <span className="text-base font-bold text-text-primary whitespace-nowrap">React Flow</span>
        <span className="text-xs text-text-muted whitespace-nowrap ml-1">
          {filteredNodes.length}/{data.nodes.length} Nodes | {filteredEdges.length}/{data.edges.length} Edges
        </span>

        <div className="flex-1" />

        {/* Layout direction selector */}
        <div className="flex items-center gap-1">
          {DIRECTIONS.map((d) => (
            <button
              key={d.key}
              onClick={() => setDirection(d.key)}
              className={`text-[10px] px-1.5 py-0.5 rounded border transition-colors ${
                direction === d.key
                  ? 'border-brand/30 text-brand bg-brand/5'
                  : 'border-border text-text-muted hover:bg-neutral-bg3'
              }`}
              title={d.label}
            >
              {d.label}
            </button>
          ))}
        </div>

        <div className="w-px h-4 bg-border mx-0.5" />

        <button
          onClick={() => fitView({ padding: 0.15, duration: 400 })}
          className="text-text-muted hover:text-text-primary text-xs px-1"
          title="Fit to view"
        >⤢</button>
        <button
          onClick={refetch}
          className={`text-text-muted hover:text-text-primary text-xs px-1 ${loading ? 'animate-spin' : ''}`}
          title="Refresh"
        >⟳</button>
      </div>

      {/* Node filter bar */}
      {availableLabels.length > 0 && (
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border shrink-0">
          <span className="text-xs font-medium text-text-muted whitespace-nowrap">● Nodes</span>
          <div className="flex items-center gap-1 flex-wrap">
            {availableLabels.map((label) => {
              const active = activeLabels.length === 0 || activeLabels.includes(label);
              return (
                <button
                  key={label}
                  onClick={() =>
                    setActiveLabels((prev) =>
                      prev.includes(label)
                        ? prev.filter((x) => x !== label)
                        : [...prev, label]
                    )
                  }
                  className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs
                             border transition-colors ${
                    active
                      ? 'border-border-strong text-text-secondary'
                      : 'border-transparent text-text-muted opacity-40'
                  }`}
                >
                  <span
                    className="h-2.5 w-2.5 rounded-full shrink-0"
                    style={{ backgroundColor: getColor(label) }}
                  />
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Error state */}
      {error && <div className="text-xs text-status-error px-3 py-1">{error}</div>}

      {/* Loading state */}
      {loading && data.nodes.length === 0 && (
        <div className="flex-1 flex items-center justify-center">
          <span className="text-xs text-text-muted animate-pulse">Loading topology…</span>
        </div>
      )}

      {/* React Flow canvas */}
      <div className="flex-1 min-h-0" style={{ width, height: height - 80 }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{
            type: 'topology',
            animated: true,
          }}
        >
          <Background color="var(--color-border-subtle)" gap={20} size={1} />
          <Controls
            showInteractive={false}
            className="!bg-neutral-bg3 !border-border !shadow-lg [&>button]:!bg-neutral-bg2 [&>button]:!border-border [&>button]:!fill-text-muted [&>button:hover]:!bg-neutral-bg4"
          />
          <MiniMap
            nodeColor={(node) => {
              const label = (node.data as { label?: string })?.label ?? '';
              return labelColor(label);
            }}
            maskColor="rgba(0, 0, 0, 0.3)"
            className="!bg-neutral-bg2 !border-border"
          />
        </ReactFlow>
      </div>
    </div>
  );
}

/**
 * Outer wrapper — provides the ReactFlowProvider context
 * required by useReactFlow() inside ReactFlowInner.
 */
export function ReactFlowTopologyViewer(props: ReactFlowTopologyViewerProps) {
  return (
    <ReactFlowProvider>
      <ReactFlowInner {...props} />
    </ReactFlowProvider>
  );
}
