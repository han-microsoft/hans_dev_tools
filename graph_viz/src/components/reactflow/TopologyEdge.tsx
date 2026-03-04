/**
 * @module TopologyEdge (React Flow custom edge)
 *
 * Custom animated edge with a floating label badge showing the
 * relationship type. Uses a smoothstep path with animated dashes
 * to indicate directionality.
 *
 * Edge colour is resolved from the EDGE_COLOR_PALETTE using the
 * same hash logic as the force-graph backend for consistency.
 */
import { memo } from 'react';
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps,
} from '@xyflow/react';
import { EDGE_COLOR_PALETTE, COLOR_PALETTE } from '@/constants/graphConstants';

/** Deterministic hash-to-palette colour for edge labels. */
function edgeLabelColor(label: string): string {
  const palette = EDGE_COLOR_PALETTE.length > 0 ? EDGE_COLOR_PALETTE : COLOR_PALETTE;
  let hash = 0;
  for (let i = 0; i < label.length; i++) hash = (hash * 31 + label.charCodeAt(i)) | 0;
  return palette[Math.abs(hash) % palette.length];
}

/**
 * Custom React Flow edge — smoothstep path with animated dashes
 * and a floating label badge at the midpoint.
 */
export const TopologyEdgeComponent = memo(function TopologyEdgeComponent({
  id,
  sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition,
  data,
  markerEnd,
}: EdgeProps) {
  const label = (data?.label as string) ?? '';
  const color = edgeLabelColor(label);

  /* Compute the smoothstep path and label position. */
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition,
    borderRadius: 12,
  });

  return (
    <>
      {/* The edge line — animated dash pattern shows flow direction. */}
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: color,
          strokeWidth: 1.5,
          strokeDasharray: '6 3',
          animation: 'dash-flow 1s linear infinite',
        }}
      />

      {/* Floating label badge at the edge midpoint. */}
      <EdgeLabelRenderer>
        <div
          className="absolute bg-neutral-bg3 border border-border rounded px-1.5 py-0.5
                     text-[9px] text-text-muted pointer-events-none shadow-sm
                     -translate-x-1/2 -translate-y-1/2"
          style={{ left: labelX, top: labelY }}
        >
          {label}
        </div>
      </EdgeLabelRenderer>
    </>
  );
});
