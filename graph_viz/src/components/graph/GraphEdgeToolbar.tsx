/**
 * @module GraphEdgeToolbar
 *
 * Edge type filter toolbar — a horizontal strip of toggleable
 * edge-type chips displayed below the node toolbar.
 *
 * Mirrors GraphToolbar but for edges. Uses EDGE_COLOR_PALETTE for
 * default colours with a deterministic hash-based fallback.
 */
import { ScrollableBar } from './ScrollableBar';
import { COLOR_PALETTE, EDGE_COLOR_PALETTE } from '@/constants/graphConstants';

interface GraphEdgeToolbarProps {
  availableEdgeLabels: string[];
  activeEdgeLabels: string[];
  onToggleEdgeLabel: (label: string) => void;
  edgeColorOverride: Record<string, string>;
}

/** Resolve edge colour: override → palette hash. */
function getEdgeColor(label: string, overrides: Record<string, string>): string {
  if (overrides[label]) return overrides[label];
  const palette = EDGE_COLOR_PALETTE.length > 0 ? EDGE_COLOR_PALETTE : COLOR_PALETTE;
  let hash = 0;
  for (let i = 0; i < label.length; i++) hash = (hash * 31 + label.charCodeAt(i)) | 0;
  return palette[Math.abs(hash) % palette.length];
}

export function GraphEdgeToolbar({
  availableEdgeLabels, activeEdgeLabels, onToggleEdgeLabel,
  edgeColorOverride,
}: GraphEdgeToolbarProps) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border shrink-0">
      <span className="text-xs font-medium text-text-muted whitespace-nowrap">━ Edges</span>

      <ScrollableBar className="flex-1 ml-1">
        {availableEdgeLabels.map((label) => {
          const active = activeEdgeLabels.length === 0 || activeEdgeLabels.includes(label);
          const color = getEdgeColor(label, edgeColorOverride);
          return (
            <span
              key={label}
              className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs
                         border transition-colors shrink-0
                         ${active
                           ? 'border-border-strong text-text-secondary'
                           : 'border-transparent text-text-muted opacity-40'}`}
            >
              <span
                className="h-2.5 w-2.5 rounded-full shrink-0"
                style={{ backgroundColor: color }}
              />
              <button
                className="hover:text-text-primary transition-colors"
                onClick={() => onToggleEdgeLabel(label)}
              >
                {label}
              </button>
            </span>
          );
        })}
      </ScrollableBar>
    </div>
  );
}
