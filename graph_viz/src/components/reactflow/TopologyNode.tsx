/**
 * @module TopologyNode (React Flow custom node)
 *
 * Rich card-style node for network topology visualization.
 * Renders as a real DOM element with:
 *   - Coloured left border strip (by node label/type)
 *   - Node ID as title
 *   - Label badge
 *   - Property key-value pairs
 *   - Status indicator dot
 *   - Source and target handles for edge connections
 *
 * Uses the same colour palette as the force-graph backend
 * for visual consistency across backends.
 */
import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import { COLOR_PALETTE } from '@/constants/graphConstants';

/** Deterministic hash-to-palette colour — matches useNodeColor logic. */
function labelColor(label: string): string {
  let hash = 0;
  for (const ch of label) hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0;
  return COLOR_PALETTE[Math.abs(hash) % COLOR_PALETTE.length];
}

/** Data shape passed via the React Flow node's `data` field. */
interface TopologyNodeData {
  nodeId: string;
  label: string;
  properties: Record<string, unknown>;
  colorOverride?: string;
}

/**
 * Custom React Flow node — renders a rich card for each topology element.
 *
 * The node is a real DOM element, so it supports full CSS styling,
 * hover effects, and can contain any React children.
 */
export const TopologyNodeComponent = memo(function TopologyNodeComponent({
  data,
}: {
  data: TopologyNodeData;
}) {
  const color = data.colorOverride ?? labelColor(data.label);
  const status = (data.properties.status as string) ?? 'unknown';

  /* Status dot colour: green=active, yellow=degraded, red=down, grey=unknown. */
  const statusColor =
    status === 'active' ? '#10B981' :
    status === 'degraded' ? '#FBBF24' :
    status === 'down' ? '#EF4444' :
    '#787878';

  return (
    <>
      {/* Target handle — edges arrive here (top for TB layout). */}
      <Handle type="target" position={Position.Top} className="!bg-transparent !border-0 !w-3 !h-1" />

      <div
        className="bg-neutral-bg2 border border-border rounded-lg shadow-md
                   hover:shadow-lg hover:border-brand/40 transition-all duration-150
                   min-w-[180px] max-w-[240px] overflow-hidden"
        style={{ borderLeftWidth: 3, borderLeftColor: color }}
      >
        {/* Header — node ID + status dot */}
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border-subtle">
          <span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: statusColor }} />
          <span className="text-xs font-semibold text-text-primary truncate">{data.nodeId}</span>
        </div>

        {/* Label badge */}
        <div className="px-3 pt-1.5">
          <span
            className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded-full"
            style={{ backgroundColor: color + '22', color }}
          >
            {data.label}
          </span>
        </div>

        {/* Properties — show first 4 key-value pairs */}
        <div className="px-3 py-1.5 space-y-0.5">
          {Object.entries(data.properties)
            .filter(([k]) => k !== 'status' && !k.startsWith('_'))
            .slice(0, 4)
            .map(([key, val]) => (
              <div key={key} className="flex items-baseline gap-1 text-[10px]">
                <span className="text-text-muted shrink-0">{key}:</span>
                <span className="text-text-secondary truncate">{String(val)}</span>
              </div>
            ))}
        </div>
      </div>

      {/* Source handle — edges depart from here (bottom for TB layout). */}
      <Handle type="source" position={Position.Bottom} className="!bg-transparent !border-0 !w-3 !h-1" />
    </>
  );
});
