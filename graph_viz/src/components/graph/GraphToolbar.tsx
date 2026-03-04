/**
 * @module GraphToolbar
 *
 * Node type filter toolbar — a horizontal strip of toggleable
 * node-type chips displayed below the graph header.
 *
 * Each chip shows a colour dot and the label name. Active chips have
 * a strong border; inactive chips are muted at 40% opacity.
 */
import { useNodeColor } from '@/hooks/useNodeColor';
import { ScrollableBar } from './ScrollableBar';

interface GraphToolbarProps {
  availableLabels: string[];
  activeLabels: string[];
  onToggleLabel: (label: string) => void;
  nodeColorOverride: Record<string, string>;
}

export function GraphToolbar({
  availableLabels, activeLabels, onToggleLabel,
  nodeColorOverride,
}: GraphToolbarProps) {
  const getColor = useNodeColor(nodeColorOverride);

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border shrink-0">
      <span className="text-xs font-medium text-text-muted whitespace-nowrap">● Nodes</span>

      <ScrollableBar className="flex-1 ml-1">
        {availableLabels.map((label) => {
          /* If no labels are actively filtered, all are visible. */
          const active = activeLabels.length === 0 || activeLabels.includes(label);
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
                style={{ backgroundColor: getColor(label) }}
              />
              <button
                className="hover:text-text-primary transition-colors"
                onClick={() => onToggleLabel(label)}
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
