/**
 * @module App
 *
 * Root application component — renders the graph topology visualizer
 * with switchable rendering backends via a tab bar.
 *
 * Backends:
 *   - Force Graph (react-force-graph-2d) — physics-based force-directed layout
 *   - React Flow (@xyflow/react) — hierarchical dagre layout with rich DOM nodes
 *
 * Both backends consume the same topology.json data via useTopology.
 * The tab bar persists the selected backend to localStorage.
 */
import { useState, useEffect, useRef } from 'react';
import { GraphTopologyViewer } from '@/components/graph';
import { ReactFlowTopologyViewer } from '@/components/reactflow';

/** Available rendering backends. */
const BACKENDS = [
  { id: 'force-graph', label: 'Force Graph', icon: '◉', description: 'Physics simulation' },
  { id: 'react-flow', label: 'React Flow', icon: '⬡', description: 'Hierarchical layout' },
] as const;

type BackendId = (typeof BACKENDS)[number]['id'];

export default function App() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });

  /* Persist selected backend to localStorage. */
  const [activeBackend, setActiveBackend] = useState<BackendId>(() => {
    const stored = localStorage.getItem('graph-backend');
    if (stored === 'force-graph' || stored === 'react-flow') return stored;
    return 'force-graph';
  });

  useEffect(() => {
    localStorage.setItem('graph-backend', activeBackend);
  }, [activeBackend]);

  /* Track container size changes via ResizeObserver. */
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        setDimensions({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
      }
    });

    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  /* Height reserved for the tab bar. */
  const TAB_BAR_HEIGHT = 40;
  /* Available height for the graph viewer (total minus tab bar). */
  const viewerHeight = dimensions.height - TAB_BAR_HEIGHT;

  return (
    <div ref={containerRef} className="h-screen w-screen overflow-hidden bg-neutral-bg1 flex flex-col">
      {/* ── Tab bar — backend switcher ────────────────────────── */}
      <div
        className="flex items-center gap-1 px-3 border-b border-border shrink-0 bg-neutral-bg2"
        style={{ height: TAB_BAR_HEIGHT }}
      >
        <span className="text-xs font-medium text-text-muted mr-2">Backend:</span>
        {BACKENDS.map((backend) => (
          <button
            key={backend.id}
            onClick={() => setActiveBackend(backend.id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-t text-xs font-medium
                       transition-all duration-150 border border-b-0 -mb-px
                       ${activeBackend === backend.id
                         ? 'bg-neutral-bg1 border-border text-text-primary shadow-sm'
                         : 'bg-transparent border-transparent text-text-muted hover:text-text-secondary hover:bg-neutral-bg3'
                       }`}
          >
            <span className="text-sm">{backend.icon}</span>
            <span>{backend.label}</span>
            <span className="text-[10px] text-text-muted hidden sm:inline">— {backend.description}</span>
          </button>
        ))}
      </div>

      {/* ── Active backend viewer ─────────────────────────────── */}
      {dimensions.width > 0 && viewerHeight > 0 && (
        <div className="flex-1 min-h-0">
          {activeBackend === 'force-graph' && (
            <GraphTopologyViewer width={dimensions.width} height={viewerHeight} />
          )}
          {activeBackend === 'react-flow' && (
            <ReactFlowTopologyViewer width={dimensions.width} height={viewerHeight} />
          )}
        </div>
      )}
    </div>
  );
}
