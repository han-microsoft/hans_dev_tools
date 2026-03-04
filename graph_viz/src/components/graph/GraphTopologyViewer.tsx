/**
 * @module GraphTopologyViewer
 *
 * Main graph container — orchestrates the full network topology
 * visualisation panel.
 *
 * Fetches topology data via {@link useTopology} and composes all graph
 * sub-components into a cohesive interactive viewer:
 *   - GraphHeaderBar   — title, counts, search, pause/refresh controls
 *   - GraphToolbar     — node type filter chips
 *   - GraphEdgeToolbar — edge type filter chips
 *   - GraphCanvas      — force-directed graph renderer
 *   - GraphTooltip     — floating hover tooltip
 *   - GraphContextMenu — right-click context menu
 *
 * Manages user customisation state (node display field, node/edge colour
 * overrides, label style, active label filters) and persists to localStorage.
 */
import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { useTopology, TopologyNode, TopologyEdge } from '@/hooks/useTopology';
import { GraphCanvas, GraphCanvasHandle } from './GraphCanvas';
import { GraphHeaderBar } from './GraphHeaderBar';
import { GraphToolbar } from './GraphToolbar';
import { GraphEdgeToolbar } from './GraphEdgeToolbar';
import { GraphTooltip } from './GraphTooltip';
import { GraphContextMenu } from './GraphContextMenu';
import { usePausableSimulation } from '@/hooks/usePausableSimulation';
import { useTooltipTracking } from '@/hooks/useTooltipTracking';
import { useNodeColor } from '@/hooks/useNodeColor';
import { COLOR_PALETTE, EDGE_COLOR_PALETTE } from '@/constants/graphConstants';

interface GraphTopologyViewerProps {
  width: number;
  height: number;
}

export function GraphTopologyViewer({ width, height }: GraphTopologyViewerProps) {
  const { data, loading, error, refetch } = useTopology();
  const canvasRef = useRef<GraphCanvasHandle>(null);

  /* Simulation pause/resume wiring. */
  const { isPaused, handleMouseEnter, handleMouseLeave, handleTogglePause, resetPause } =
    usePausableSimulation(canvasRef);

  /* Tooltip tracking for node/edge hover. */
  const { tooltip, handleNodeHover, handleLinkHover } =
    useTooltipTracking<TopologyNode, TopologyEdge>();

  /* Right-click context menu state. */
  const [contextMenu, setContextMenu] = useState<{
    x: number; y: number; node: TopologyNode;
  } | null>(null);

  const handleNodeRightClick = useCallback((node: TopologyNode, event: MouseEvent) => {
    event.preventDefault();
    setContextMenu({ x: event.clientX, y: event.clientY, node });
  }, []);

  /* Track data version changes to trigger zoom-to-fit on new data. */
  const [dataVersion, setDataVersion] = useState(0);
  const prevNodeCountRef = useRef(data.nodes.length);
  useEffect(() => {
    if (data.nodes.length !== prevNodeCountRef.current) {
      prevNodeCountRef.current = data.nodes.length;
      setDataVersion((v) => v + 1);
    }
  }, [data.nodes.length]);

  /* ── User customisation state (persisted to localStorage) ─────── */

  /** Per-label display field: which property to show as node text. */
  const [nodeDisplayField, setNodeDisplayField] = useState<Record<string, string>>(() => {
    try {
      const stored = localStorage.getItem('graph-display-fields');
      return stored ? JSON.parse(stored) : {};
    } catch { return {}; }
  });

  /** Per-label node colour overrides. */
  const [nodeColorOverride, setNodeColorOverride] = useState<Record<string, string>>(() => {
    try {
      const stored = localStorage.getItem('graph-colors');
      return stored ? JSON.parse(stored) : {};
    } catch { return {}; }
  });

  /** Active node label filters — empty = all visible. */
  const [activeLabels, setActiveLabels] = useState<string[]>([]);
  const [showNodeBar, setShowNodeBar] = useState(true);
  const [showEdgeBar, setShowEdgeBar] = useState(true);
  const [activeEdgeLabels, setActiveEdgeLabels] = useState<string[]>([]);

  /** Per-label edge colour overrides. */
  const [edgeColorOverride, setEdgeColorOverride] = useState<Record<string, string>>(() => {
    try {
      const stored = localStorage.getItem('graph-edge-colors');
      return stored ? JSON.parse(stored) : {};
    } catch { return {}; }
  });

  /** Label style: font sizes, colours, node scale, edge width. */
  const [labelStyle, setLabelStyle] = useState<{
    nodeFontSize: number | null;
    nodeColor: string | null;
    edgeFontSize: number | null;
    edgeColor: string | null;
    nodeScale: number;
    edgeWidth: number;
  }>(() => {
    try {
      const stored = localStorage.getItem('graph-label-style');
      const parsed = stored ? JSON.parse(stored) : {};
      return { nodeFontSize: null, nodeColor: null, edgeFontSize: null, edgeColor: null, nodeScale: 1, edgeWidth: 1.5, ...parsed };
    } catch { return { nodeFontSize: null, nodeColor: null, edgeFontSize: null, edgeColor: null, nodeScale: 1, edgeWidth: 1.5 }; }
  });

  /* Persist customisation to localStorage on change. */
  useEffect(() => { localStorage.setItem('graph-display-fields', JSON.stringify(nodeDisplayField)); }, [nodeDisplayField]);
  useEffect(() => { localStorage.setItem('graph-colors', JSON.stringify(nodeColorOverride)); }, [nodeColorOverride]);
  useEffect(() => { localStorage.setItem('graph-edge-colors', JSON.stringify(edgeColorOverride)); }, [edgeColorOverride]);
  useEffect(() => { localStorage.setItem('graph-label-style', JSON.stringify(labelStyle)); }, [labelStyle]);

  /** Distinct edge labels computed from data. */
  const availableEdgeLabels = useMemo(
    () => [...new Set(data.edges.map((e: TopologyEdge) => e.label))].sort(),
    [data.edges],
  );

  /* ── Filtering ─────────────────────────────────────────────────── */

  /** Nodes filtered by active label set. */
  const filteredNodes = data.nodes.filter((n) => {
    if (activeLabels.length > 0 && !activeLabels.includes(n.label)) return false;
    return true;
  });

  /** Id set for fast edge endpoint lookup. */
  const nodeIdSet = new Set(filteredNodes.map((n) => n.id));

  /** Edges filtered by visible nodes and active edge label set. */
  const filteredEdges = data.edges.filter((e) => {
    const srcId = typeof e.source === 'string' ? e.source : e.source.id;
    const tgtId = typeof e.target === 'string' ? e.target : e.target.id;
    if (!nodeIdSet.has(srcId) || !nodeIdSet.has(tgtId)) return false;
    if (activeEdgeLabels.length > 0 && !activeEdgeLabels.includes(e.label)) return false;
    return true;
  });

  /* Height calculation: header bar + optional node bar + optional edge bar. */
  const BAR_HEIGHT = 36;
  const TOOLBAR_HEIGHT = BAR_HEIGHT + (showNodeBar ? BAR_HEIGHT : 0) + (showEdgeBar ? BAR_HEIGHT : 0);

  return (
    <div className="h-full flex flex-col overflow-hidden border border-border bg-neutral-bg1">
      <GraphHeaderBar
        loading={loading}
        isPaused={isPaused}
        onTogglePause={handleTogglePause}
        onZoomToFit={() => canvasRef.current?.zoomToFit()}
        onRefresh={() => { refetch(); resetPause(); }}
        showNodeBar={showNodeBar}
        onToggleNodeBar={() => setShowNodeBar((v) => !v)}
        showEdgeBar={showEdgeBar}
        onToggleEdgeBar={() => setShowEdgeBar((v) => !v)}
        visibleNodeCount={filteredNodes.length}
        totalNodeCount={data.nodes.length}
        visibleEdgeCount={filteredEdges.length}
        totalEdgeCount={data.edges.length}
        nodeLabelFontSize={labelStyle.nodeFontSize}
        onNodeLabelFontSizeChange={(s) => setLabelStyle((prev) => ({ ...prev, nodeFontSize: s }))}
        nodeLabelColor={labelStyle.nodeColor}
        onNodeLabelColorChange={(c) => setLabelStyle((prev) => ({ ...prev, nodeColor: c }))}
        edgeLabelFontSize={labelStyle.edgeFontSize}
        onEdgeLabelFontSizeChange={(s) => setLabelStyle((prev) => ({ ...prev, edgeFontSize: s }))}
        edgeLabelColor={labelStyle.edgeColor}
        onEdgeLabelColorChange={(c) => setLabelStyle((prev) => ({ ...prev, edgeColor: c }))}
        nodeScale={labelStyle.nodeScale}
        onNodeScaleChange={(s) => setLabelStyle((prev) => ({ ...prev, nodeScale: s }))}
        edgeWidth={labelStyle.edgeWidth}
        onEdgeWidthChange={(w) => setLabelStyle((prev) => ({ ...prev, edgeWidth: w }))}
        nodeLabels={data.meta?.labels ?? []}
        edgeLabels={availableEdgeLabels}
        nodeColorOverride={nodeColorOverride}
        edgeColorOverride={edgeColorOverride}
        getNodeColor={useNodeColor(nodeColorOverride)}
        getEdgeColor={(label: string) => {
          if (edgeColorOverride[label]) return edgeColorOverride[label];
          const palette = EDGE_COLOR_PALETTE.length > 0 ? EDGE_COLOR_PALETTE : COLOR_PALETTE;
          let hash = 0;
          for (let i = 0; i < label.length; i++) hash = (hash * 31 + label.charCodeAt(i)) | 0;
          return palette[Math.abs(hash) % palette.length];
        }}
        onSetNodeColor={(label, color) =>
          setNodeColorOverride((prev) => ({ ...prev, [label]: color }))
        }
        onSetEdgeColor={(label, color) =>
          setEdgeColorOverride((prev) => ({ ...prev, [label]: color }))
        }
        onSearch={(query) => {
          /* Find the first node whose id/label/properties match the query. */
          const q = query.toLowerCase();
          const match = filteredNodes.find((n) =>
            n.id.toLowerCase().includes(q) ||
            n.label.toLowerCase().includes(q) ||
            Object.values(n.properties).some((v) => String(v).toLowerCase().includes(q))
          );
          if (match && match.x != null && match.y != null) {
            canvasRef.current?.centerOnNode(match.x, match.y);
          }
        }}
      />

      {/* Node type filter bar */}
      {showNodeBar && (
        <GraphToolbar
          availableLabels={data.meta?.labels ?? []}
          activeLabels={activeLabels}
          onToggleLabel={(l) =>
            setActiveLabels((prev) =>
              prev.includes(l) ? prev.filter((x) => x !== l) : [...prev, l]
            )
          }
          nodeColorOverride={nodeColorOverride}
        />
      )}

      {/* Edge type filter bar */}
      {showEdgeBar && (
        <GraphEdgeToolbar
          availableEdgeLabels={availableEdgeLabels}
          activeEdgeLabels={activeEdgeLabels}
          onToggleEdgeLabel={(l) =>
            setActiveEdgeLabels((prev) =>
              prev.includes(l) ? prev.filter((x) => x !== l) : [...prev, l]
            )
          }
          edgeColorOverride={edgeColorOverride}
        />
      )}

      {/* Error state */}
      {error && (
        <div className="text-xs text-status-error px-3 py-1">{error}</div>
      )}

      {/* Loading state */}
      {loading && data.nodes.length === 0 && (
        <div className="flex-1 flex items-center justify-center">
          <span className="text-xs text-text-muted animate-pulse">Loading topology…</span>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && data.nodes.length === 0 && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center space-y-2">
            <p className="text-sm text-text-muted">No graph data available</p>
            <p className="text-xs text-text-muted/60">
              Place a topology.json in the public/ directory to populate the graph.
            </p>
          </div>
        </div>
      )}

      {/* Canvas */}
      <div className="flex-1 min-h-0 relative">
        <GraphCanvas
          ref={canvasRef}
          nodes={filteredNodes}
          edges={filteredEdges}
          width={width}
          height={height - TOOLBAR_HEIGHT}
          nodeDisplayField={nodeDisplayField}
          nodeColorOverride={nodeColorOverride}
          edgeColorOverride={edgeColorOverride}
          dataVersion={dataVersion}
          nodeLabelFontSize={labelStyle.nodeFontSize}
          nodeLabelColor={labelStyle.nodeColor}
          edgeLabelFontSize={labelStyle.edgeFontSize}
          edgeLabelColor={labelStyle.edgeColor}
          nodeScale={labelStyle.nodeScale}
          edgeWidth={labelStyle.edgeWidth}
          onNodeHover={handleNodeHover}
          onLinkHover={handleLinkHover}
          onNodeRightClick={handleNodeRightClick}
          onBackgroundClick={() => setContextMenu(null)}
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        />

        {/* Paused indicator */}
        {isPaused && (
          <div className="absolute bottom-2 right-2 px-2 py-0.5 rounded-full
                         bg-neutral-bg4 text-text-muted text-[10px]
                         transition-opacity duration-100">
            ⏸ Paused
          </div>
        )}
      </div>

      {/* Overlays */}
      <GraphTooltip tooltip={tooltip} nodeColorOverride={nodeColorOverride} />
      <GraphContextMenu
        menu={contextMenu}
        onClose={() => setContextMenu(null)}
        onSetDisplayField={(label, field) =>
          setNodeDisplayField((prev) => ({ ...prev, [label]: field }))
        }
      />
    </div>
  );
}
