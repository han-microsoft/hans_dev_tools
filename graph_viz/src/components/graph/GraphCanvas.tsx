/**
 * @module GraphCanvas
 *
 * Force-directed graph renderer — the interactive 2D canvas that
 * visualises the network topology.
 *
 * Wraps `react-force-graph-2d` with custom canvas rendering callbacks:
 *   - Nodes: filled circles with label text, coloured per node type
 *   - Edges: lines with optional arrow heads and relationship labels
 *
 * Supports user interaction: hover tooltips, right-click context menu,
 * click to zoom, drag to reposition, scroll to zoom.
 *
 * Exposes a {@link GraphCanvasHandle} imperative handle for
 * parent-controlled actions (zoomToFit, setFrozen, centerOnNode).
 */
import { useState, useRef, useCallback, useEffect, forwardRef, useImperativeHandle } from 'react';
import ForceGraph2D, { ForceGraphMethods, NodeObject, LinkObject } from 'react-force-graph-2d';
import type { TopologyNode, TopologyEdge } from '@/hooks/useTopology';
import { useNodeColor } from '@/hooks/useNodeColor';

type GNode = NodeObject<TopologyNode>;
type GLink = LinkObject<TopologyNode, TopologyEdge>;

/** Imperative handle exposed to parent via ref. */
export interface GraphCanvasHandle {
  zoomToFit: () => void;
  setFrozen: (frozen: boolean) => void;
  centerOnNode: (x: number, y: number) => void;
}

interface GraphCanvasProps {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  width: number;
  height: number;
  nodeDisplayField: Record<string, string>;
  nodeColorOverride: Record<string, string>;
  dataVersion: number;
  nodeLabelFontSize?: number | null;
  nodeLabelColor?: string | null;
  edgeLabelFontSize?: number | null;
  edgeLabelColor?: string | null;
  nodeScale?: number;
  edgeWidth?: number;
  edgeColorOverride?: Record<string, string>;
  onNodeHover: (node: TopologyNode | null) => void;
  onLinkHover: (edge: TopologyEdge | null) => void;
  onNodeRightClick: (node: TopologyNode, event: MouseEvent) => void;
  onBackgroundClick: () => void;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}

export const GraphCanvas = forwardRef<GraphCanvasHandle, GraphCanvasProps>(
  function GraphCanvas(
    { nodes, edges, width, height,
      nodeDisplayField, nodeColorOverride, dataVersion,
      nodeLabelFontSize, nodeLabelColor,
      edgeLabelFontSize, edgeLabelColor,
      nodeScale = 1,
      edgeWidth,
      edgeColorOverride,
      onNodeHover, onLinkHover, onNodeRightClick, onBackgroundClick,
      onMouseEnter, onMouseLeave },
    ref,
  ) {
    const fgRef = useRef<ForceGraphMethods<GNode, GLink> | undefined>(undefined);
    const [frozen, setFrozen] = useState(false);

    /* Expose imperative methods to parent component. */
    useImperativeHandle(ref, () => ({
      zoomToFit: () => fgRef.current?.zoomToFit(400, 40),
      setFrozen: (f: boolean) => {
        setFrozen(f);
        if (!f) fgRef.current?.d3ReheatSimulation();
      },
      centerOnNode: (x: number, y: number) => {
        fgRef.current?.centerAt(x, y, 600);
        fgRef.current?.zoom(4, 600);
      },
    }), []);

    /* Auto zoom-to-fit when data changes. */
    useEffect(() => {
      if (fgRef.current && nodes.length > 0) {
        setTimeout(() => fgRef.current?.zoomToFit(400, 40), 500);
      }
    }, [dataVersion, nodes.length]);

    const getNodeColor = useNodeColor(nodeColorOverride);

    /* Resolve theme CSS variables for canvas rendering. */
    const [themeColors, setThemeColors] = useState(() => {
      const s = getComputedStyle(document.documentElement);
      return {
        textPrimary: s.getPropertyValue('--color-text-primary').trim(),
        textMuted: s.getPropertyValue('--color-text-muted').trim(),
        borderDefault: s.getPropertyValue('--color-border-default').trim(),
        borderStrong: s.getPropertyValue('--color-border-strong').trim(),
      };
    });

    /* Watch for theme class changes (light/dark toggle). */
    useEffect(() => {
      const observer = new MutationObserver(() => {
        const s = getComputedStyle(document.documentElement);
        setThemeColors({
          textPrimary: s.getPropertyValue('--color-text-primary').trim(),
          textMuted: s.getPropertyValue('--color-text-muted').trim(),
          borderDefault: s.getPropertyValue('--color-border-default').trim(),
          borderStrong: s.getPropertyValue('--color-border-strong').trim(),
        });
      });
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
      return () => observer.disconnect();
    }, []);

    /** Custom node renderer — filled circle + label text below. */
    const nodeCanvasObject = useCallback(
      (node: GNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
        const size = (Number(node.properties['_size']) || 6) * nodeScale;
        const color = getNodeColor(node.label);
        const { textPrimary, borderDefault } = themeColors;

        /* Draw the circle. */
        ctx.beginPath();
        ctx.arc(node.x!, node.y!, size, 0, 2 * Math.PI);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = borderDefault;
        ctx.lineWidth = 0.5;
        ctx.stroke();

        /* Determine which property to show as label text. */
        const displayField = nodeDisplayField[node.label] ?? 'id';
        const label = displayField === 'id'
          ? node.id
          : String(node.properties[displayField] ?? node.id);

        /* Auto-scale font size based on zoom level. */
        const autoSize = Math.max(10 / globalScale, 3);
        const fontSize = nodeLabelFontSize != null ? nodeLabelFontSize / globalScale : autoSize;
        if (fontSize > 0) {
          ctx.font = `${fontSize}px 'Segoe UI', system-ui, sans-serif`;
          ctx.fillStyle = nodeLabelColor ?? textPrimary;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'top';
          ctx.fillText(label, node.x!, node.y! + size + 2);
        }
      },
      [getNodeColor, nodeDisplayField, themeColors, nodeLabelFontSize, nodeLabelColor, nodeScale],
    );

    /** Edge label rendering mode — draw labels after the link line. */
    const linkCanvasObjectMode = () => 'after' as const;

    /** Custom edge label renderer — relationship text at midpoint. */
    const linkCanvasObject = useCallback(
      (link: GLink, ctx: CanvasRenderingContext2D, globalScale: number) => {
        const src = link.source as GNode;
        const tgt = link.target as GNode;
        if (!src.x || !tgt.x) return;

        const midX = (src.x + tgt.x) / 2;
        const midY = (src.y! + tgt.y!) / 2;
        const autoSize = Math.max(8 / globalScale, 2.5);
        const fontSize = edgeLabelFontSize != null ? edgeLabelFontSize / globalScale : autoSize;

        if (fontSize > 0) {
          ctx.font = `${fontSize}px 'Segoe UI', system-ui, sans-serif`;
          ctx.fillStyle = edgeLabelColor ?? themeColors.textMuted;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(link.label, midX, midY);
        }
      },
      [themeColors, edgeLabelFontSize, edgeLabelColor],
    );

    /** Double-click a node to center and zoom in. */
    const handleNodeDoubleClick = useCallback((node: GNode) => {
      fgRef.current?.centerAt(node.x, node.y, 600);
      fgRef.current?.zoom(4, 600);
    }, []);

    const handleNodeHoverInternal = useCallback(
      (node: GNode | null) => onNodeHover(node as TopologyNode | null),
      [onNodeHover],
    );
    const handleLinkHoverInternal = useCallback(
      (link: GLink | null) => onLinkHover(link as TopologyEdge | null),
      [onLinkHover],
    );

    return (
      <div
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
        style={{ width, height, touchAction: 'none' }}
      >
        <ForceGraph2D
          ref={fgRef}
          width={width}
          height={height}
          graphData={{ nodes: nodes as GNode[], links: edges as GLink[] }}
          backgroundColor="transparent"
          nodeCanvasObject={nodeCanvasObject}
          nodeCanvasObjectMode={() => 'replace'}
          nodeId="id"
          linkSource="source"
          linkTarget="target"
          linkColor={(link: GLink) => {
            if (edgeColorOverride && link.label && edgeColorOverride[link.label]) {
              return edgeColorOverride[link.label];
            }
            return themeColors.borderDefault;
          }}
          linkWidth={edgeWidth ?? 1.5}
          linkDirectionalArrowLength={4}
          linkDirectionalArrowRelPos={0.9}
          linkDirectionalArrowColor={() => themeColors.borderStrong}
          linkCanvasObjectMode={linkCanvasObjectMode}
          linkCanvasObject={linkCanvasObject}
          onNodeHover={handleNodeHoverInternal}
          onLinkHover={handleLinkHoverInternal}
          onNodeRightClick={onNodeRightClick as (node: GNode, event: MouseEvent) => void}
          onNodeClick={handleNodeDoubleClick}
          onBackgroundClick={onBackgroundClick}
          d3AlphaDecay={0.02}
          d3VelocityDecay={0.3}
          cooldownTicks={frozen ? 0 : Infinity}
          cooldownTime={3000}
          enableNodeDrag={true}
          enableZoomInteraction={true}
          enablePanInteraction={true}
        />
      </div>
    );
  },
);
