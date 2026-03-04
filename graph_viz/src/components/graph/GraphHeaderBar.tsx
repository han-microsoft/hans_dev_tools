/**
 * @module GraphHeaderBar
 *
 * Graph panel header bar — title, filtered counts, search, Aa label
 * style popover, color editor, node/edge toolbar toggles, and
 * simulation controls (pause/play, zoom-to-fit, refresh).
 */
import { useState, useRef } from 'react';
import { Search } from 'lucide-react';
import { ColorEditor } from './ColorEditor';

interface GraphHeaderBarProps {
  loading: boolean;
  isPaused?: boolean;
  onTogglePause?: () => void;
  onZoomToFit: () => void;
  onRefresh: () => void;
  showNodeBar: boolean;
  onToggleNodeBar: () => void;
  showEdgeBar: boolean;
  onToggleEdgeBar: () => void;
  onSearch?: (query: string) => void;
  visibleNodeCount: number;
  totalNodeCount: number;
  visibleEdgeCount: number;
  totalEdgeCount: number;
  nodeLabelFontSize?: number | null;
  onNodeLabelFontSizeChange?: (size: number | null) => void;
  nodeLabelColor?: string | null;
  onNodeLabelColorChange?: (color: string | null) => void;
  edgeLabelFontSize?: number | null;
  onEdgeLabelFontSizeChange?: (size: number | null) => void;
  edgeLabelColor?: string | null;
  onEdgeLabelColorChange?: (color: string | null) => void;
  nodeScale?: number;
  onNodeScaleChange?: (scale: number) => void;
  edgeWidth?: number;
  onEdgeWidthChange?: (width: number) => void;
  /** Color editor props */
  nodeLabels?: string[];
  edgeLabels?: string[];
  nodeColorOverride?: Record<string, string>;
  edgeColorOverride?: Record<string, string>;
  getNodeColor?: (label: string) => string;
  getEdgeColor?: (label: string) => string;
  onSetNodeColor?: (label: string, color: string) => void;
  onSetEdgeColor?: (label: string, color: string) => void;
}

export function GraphHeaderBar({
  loading,
  isPaused, onTogglePause,
  onZoomToFit, onRefresh,
  showNodeBar, onToggleNodeBar,
  showEdgeBar, onToggleEdgeBar,
  onSearch,
  visibleNodeCount, totalNodeCount,
  visibleEdgeCount, totalEdgeCount,
  nodeLabelFontSize, onNodeLabelFontSizeChange,
  nodeLabelColor, onNodeLabelColorChange,
  edgeLabelFontSize, onEdgeLabelFontSizeChange,
  edgeLabelColor, onEdgeLabelColorChange,
  nodeScale, onNodeScaleChange,
  edgeWidth, onEdgeWidthChange,
  nodeLabels, edgeLabels,
  getNodeColor, getEdgeColor, onSetNodeColor, onSetEdgeColor,
}: GraphHeaderBarProps) {
  const [searchText, setSearchText] = useState('');
  const [showLabelPopover, setShowLabelPopover] = useState(false);
  const [showColorEditor, setShowColorEditor] = useState(false);
  const aaBtnRef = useRef<HTMLButtonElement>(null);
  const colorBtnRef = useRef<HTMLButtonElement>(null);
  const [aaAnchor, setAaAnchor] = useState<DOMRect | null>(null);
  const [colorAnchor, setColorAnchor] = useState<DOMRect | null>(null);

  /** Submit the search query on Enter or button click. */
  const handleSearchSubmit = () => {
    const q = searchText.trim();
    if (q && onSearch) onSearch(q);
  };

  /** Toggle the Aa label style popover. */
  const toggleLabelPopover = () => {
    if (aaBtnRef.current) setAaAnchor(aaBtnRef.current.getBoundingClientRect());
    setShowLabelPopover((v) => !v);
    setShowColorEditor(false);
  };

  /** Toggle the color editor popover. */
  const toggleColorEditor = () => {
    if (colorBtnRef.current) setColorAnchor(colorBtnRef.current.getBoundingClientRect());
    setShowColorEditor((v) => !v);
    setShowLabelPopover(false);
  };

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border shrink-0">
      {/* Title */}
      <span className="text-base font-bold text-text-primary whitespace-nowrap flex items-center gap-1.5">Graph</span>

      {/* Counts */}
      <span className="text-xs text-text-muted whitespace-nowrap ml-1">
        {visibleNodeCount}/{totalNodeCount} Nodes | {visibleEdgeCount}/{totalEdgeCount} Edges
      </span>

      {/* Search */}
      {onSearch && (
        <div className="flex items-center gap-1 ml-2">
          <input
            type="text"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSearchSubmit(); }}
            placeholder="Find node…"
            className="bg-neutral-bg3 border border-border rounded px-2 py-0.5 text-xs text-text-primary placeholder-text-muted outline-none focus:border-brand/50 transition-colors"
            style={{ width: '140px' }}
          />
          <button
            onClick={handleSearchSubmit}
            className="text-text-muted hover:text-text-primary transition-colors p-0.5"
            title="Search"
          >
            <Search className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      <div className="flex-1" />

      {/* Aa — consolidated label style popover */}
      {onNodeLabelFontSizeChange && (
        <button
          ref={aaBtnRef}
          onClick={toggleLabelPopover}
          className={`text-sm px-2 py-1 rounded border transition-colors ${
            showLabelPopover
              ? 'border-brand/30 text-brand bg-brand/5'
              : 'border-border text-text-muted hover:bg-neutral-bg3'
          }`}
          title="Label style"
        >Aa</button>
      )}

      {/* Aa popover panel — font size, colour, node scale, edge width sliders */}
      {showLabelPopover && aaAnchor && (
        <div
          className="fixed z-50 bg-neutral-bg3 border border-border rounded-lg p-3 shadow-xl space-y-3"
          style={{ top: aaAnchor.bottom + 4, left: aaAnchor.left - 60 }}
        >
          {onNodeScaleChange && (
            <div className="space-y-1">
              <div className="text-[10px] text-text-muted uppercase tracking-wider font-medium">Node Size</div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-text-secondary w-10">Scale</span>
                <input type="range" min={0.3} max={3} step={0.1}
                  value={nodeScale ?? 1}
                  onChange={(e) => onNodeScaleChange(parseFloat(e.target.value))}
                  className="w-24 accent-brand" />
                <span className="text-[10px] text-text-muted w-6">{(nodeScale ?? 1).toFixed(1)}×</span>
              </div>
            </div>
          )}
          <div className="border-t border-border" />
          {onNodeLabelFontSizeChange && onNodeLabelColorChange && (
            <div className="space-y-1">
              <div className="text-[10px] text-text-muted uppercase tracking-wider font-medium">Node Labels</div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-text-secondary w-10">Size</span>
                <input type="range" min={0} max={30} step={0.5}
                  value={nodeLabelFontSize ?? 10}
                  onChange={(e) => { const v = parseFloat(e.target.value); onNodeLabelFontSizeChange(v === 10 ? null : v); }}
                  className="w-24 accent-brand" />
                <span className="text-[10px] text-text-muted w-6">{nodeLabelFontSize ?? 'auto'}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-text-secondary w-10">Color</span>
                <button className="h-4 w-4 rounded-full border border-border cursor-pointer hover:scale-125 transition-transform"
                  style={{ backgroundColor: nodeLabelColor ?? '#ccc' }}
                  onClick={() => onNodeLabelColorChange(nodeLabelColor === '#fff' ? null : '#fff')} title="Toggle node label color" />
                {nodeLabelColor && (
                  <button className="text-[10px] text-text-muted hover:text-text-primary" onClick={() => onNodeLabelColorChange(null)}>reset</button>
                )}
              </div>
            </div>
          )}
          <div className="border-t border-border" />
          {onEdgeLabelFontSizeChange && onEdgeLabelColorChange && (
            <div className="space-y-1">
              <div className="text-[10px] text-text-muted uppercase tracking-wider font-medium">Edge Labels</div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-text-secondary w-10">Size</span>
                <input type="range" min={0} max={20} step={0.5}
                  value={edgeLabelFontSize ?? 8}
                  onChange={(e) => { const v = parseFloat(e.target.value); onEdgeLabelFontSizeChange(v === 8 ? null : v); }}
                  className="w-24 accent-brand" />
                <span className="text-[10px] text-text-muted w-6">{edgeLabelFontSize ?? 'auto'}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-text-secondary w-10">Color</span>
                <button className="h-4 w-4 rounded-full border border-border cursor-pointer hover:scale-125 transition-transform"
                  style={{ backgroundColor: edgeLabelColor ?? '#888' }}
                  onClick={() => onEdgeLabelColorChange(edgeLabelColor === '#fff' ? null : '#fff')} title="Toggle edge label color" />
                {edgeLabelColor && (
                  <button className="text-[10px] text-text-muted hover:text-text-primary" onClick={() => onEdgeLabelColorChange(null)}>reset</button>
                )}
              </div>
            </div>
          )}
          {onEdgeWidthChange && (
            <>
              <div className="border-t border-border" />
              <div className="space-y-1">
                <div className="text-[10px] text-text-muted uppercase tracking-wider font-medium">Edge Width</div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-text-secondary w-10">Width</span>
                  <input type="range" min={0.5} max={6} step={0.5}
                    value={edgeWidth ?? 1.5}
                    onChange={(e) => onEdgeWidthChange(parseFloat(e.target.value))}
                    className="w-24 accent-brand" />
                  <span className="text-[10px] text-text-muted w-6">{(edgeWidth ?? 1.5).toFixed(1)}</span>
                </div>
              </div>
            </>
          )}
          <button className="text-[10px] text-text-muted hover:text-text-primary" onClick={() => setShowLabelPopover(false)}>Close</button>
        </div>
      )}

      {/* Color editor trigger */}
      {onSetNodeColor && (
        <button
          ref={colorBtnRef}
          onClick={toggleColorEditor}
          className={`text-sm px-2 py-1 rounded border transition-colors ${
            showColorEditor
              ? 'border-brand/30 text-brand bg-brand/5'
              : 'border-border text-text-muted hover:bg-neutral-bg3'
          }`}
          title="Edit node & edge colors"
        >🎨</button>
      )}

      {/* Color editor panel */}
      {showColorEditor && colorAnchor && nodeLabels && edgeLabels && getNodeColor && getEdgeColor && onSetNodeColor && onSetEdgeColor && (
        <div style={{ position: 'fixed', top: colorAnchor.bottom + 4, left: Math.max(4, colorAnchor.left - 180), zIndex: 50 }}>
          <ColorEditor
            nodeLabels={nodeLabels}
            edgeLabels={edgeLabels}
            getNodeColor={getNodeColor}
            getEdgeColor={getEdgeColor}
            onSetNodeColor={onSetNodeColor}
            onSetEdgeColor={onSetEdgeColor}
            onClose={() => setShowColorEditor(false)}
            excludeRef={colorBtnRef}
          />
        </div>
      )}

      {/* Toggle node/edge filter bars */}
      <button
        onClick={onToggleNodeBar}
        className={`text-xs px-2 py-1 rounded border transition-colors inline-flex items-center gap-1 ${
          showNodeBar
            ? 'border-brand/30 text-brand bg-brand/5 hover:bg-brand/10'
            : 'border-border text-text-muted hover:bg-neutral-bg3'
        }`}
        title={showNodeBar ? 'Hide node filter bar' : 'Show node filter bar'}
      >
        <span className="text-[10px]">●</span> Nodes
      </button>
      <button
        onClick={onToggleEdgeBar}
        className={`text-xs px-2 py-1 rounded border transition-colors inline-flex items-center gap-1 ${
          showEdgeBar
            ? 'border-brand/30 text-brand bg-brand/5 hover:bg-brand/10'
            : 'border-border text-text-muted hover:bg-neutral-bg3'
        }`}
        title={showEdgeBar ? 'Hide edge filter bar' : 'Show edge filter bar'}
      >
        <span className="text-[10px]">━</span> Edges
      </button>

      <div className="w-px h-4 bg-border mx-0.5" />

      {/* Simulation controls */}
      {onTogglePause && (
        <button
          onClick={onTogglePause}
          className={`text-xs px-1 transition-colors ${
            isPaused ? 'text-brand hover:text-brand/80' : 'text-text-muted hover:text-text-primary'
          }`}
          title={isPaused ? 'Resume simulation' : 'Pause simulation'}
        >{isPaused ? '▶' : '⏸'}</button>
      )}
      <button
        onClick={onZoomToFit}
        className="text-text-muted hover:text-text-primary text-xs px-1"
        title="Fit to view"
      >⤢</button>
      <button
        onClick={onRefresh}
        className={`text-text-muted hover:text-text-primary text-xs px-1
                   ${loading ? 'animate-spin' : ''}`}
        title="Refresh"
      >⟳</button>
    </div>
  );
}
