/**
 * @module ColorEditor
 *
 * Dropdown panel for editing node and edge type colors.
 * Two columns (Nodes | Edges) with a color swatch next to each label.
 * Clicking a swatch opens a shade palette grid (8 hues × 7 lightness).
 * Selecting a shade sets the color and closes the palette.
 */
import { useState, useEffect, useRef } from "react";
import type { RefObject } from "react";

/** 8 hue columns × 7 lightness rows = 56 swatches. */
const PALETTE: string[][] = [
  /* Red     */ ["#ffcdd2", "#ef9a9a", "#e57373", "#ef5350", "#f44336", "#e53935", "#c62828"],
  /* Pink    */ ["#f8bbd0", "#f48fb1", "#f06292", "#ec407a", "#e91e63", "#d81b60", "#ad1457"],
  /* Purple  */ ["#e1bee7", "#ce93d8", "#ba68c8", "#ab47bc", "#9c27b0", "#8e24aa", "#6a1b9a"],
  /* Blue    */ ["#bbdefb", "#90caf9", "#64b5f6", "#42a5f5", "#2196f3", "#1e88e5", "#1565c0"],
  /* Teal    */ ["#b2dfdb", "#80cbc4", "#4db6ac", "#26a69a", "#009688", "#00897b", "#00695c"],
  /* Green   */ ["#c8e6c9", "#a5d6a7", "#81c784", "#66bb6a", "#4caf50", "#43a047", "#2e7d32"],
  /* Yellow  */ ["#fff9c4", "#fff59d", "#fff176", "#ffee58", "#ffeb3b", "#fdd835", "#f9a825"],
  /* Orange  */ ["#ffe0b2", "#ffcc80", "#ffb74d", "#ffa726", "#ff9800", "#fb8c00", "#e65100"],
];

interface ColorEditorProps {
  /** Available node labels. */
  nodeLabels: string[];
  /** Available edge labels. */
  edgeLabels: string[];
  /** Default node color resolver. */
  getNodeColor: (label: string) => string;
  /** Default edge color resolver. */
  getEdgeColor: (label: string) => string;
  /** Set a node type color. */
  onSetNodeColor: (label: string, color: string) => void;
  /** Set an edge type color. */
  onSetEdgeColor: (label: string, color: string) => void;
  /** Close the editor. */
  onClose: () => void;
  /** Ref to the trigger button — excluded from click-outside detection. */
  excludeRef?: RefObject<HTMLElement | null>;
}

export function ColorEditor({
  nodeLabels,
  edgeLabels,
  getNodeColor,
  getEdgeColor,
  onSetNodeColor,
  onSetEdgeColor,
  onClose,
  excludeRef,
}: ColorEditorProps) {
  /** Which label's palette is open: { type, label } or null. */
  const [activePicker, setActivePicker] = useState<{
    type: "node" | "edge";
    label: string;
  } | null>(null);

  const panelRef = useRef<HTMLDivElement>(null);

  /** Close on click outside the panel (excluding the trigger button). */
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)
          && !(excludeRef?.current && excludeRef.current.contains(e.target as Node))) {
        onClose();
      }
    };
    /* Delay to avoid closing on the same click that opened it. */
    const id = setTimeout(() => document.addEventListener("mousedown", handler), 0);
    return () => {
      clearTimeout(id);
      document.removeEventListener("mousedown", handler);
    };
  }, [onClose, excludeRef]);

  /** Toggle the palette for a specific label. */
  const handleSwatchClick = (type: "node" | "edge", label: string) => {
    setActivePicker((prev) =>
      prev?.type === type && prev?.label === label ? null : { type, label }
    );
  };

  /** Apply the selected colour and close the palette. */
  const handlePaletteSelect = (color: string) => {
    if (!activePicker) return;
    if (activePicker.type === "node") {
      onSetNodeColor(activePicker.label, color);
    } else {
      onSetEdgeColor(activePicker.label, color);
    }
    setActivePicker(null);
  };

  return (
    <div
      ref={panelRef}
      className="fixed z-50 bg-neutral-bg3 border border-border rounded-lg shadow-xl p-3"
      style={{ maxHeight: "70vh", overflowY: "auto", width: "420px" }}
    >
      {/* Two-column layout: Nodes | Edges */}
      <div className="flex gap-4">
        {/* Nodes column */}
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-widest font-semibold text-text-muted mb-2">
            Nodes
          </div>
          <div className="space-y-1">
            {nodeLabels.map((label) => (
              <ColorItem
                key={label}
                label={label}
                color={getNodeColor(label)}
                isActive={activePicker?.type === "node" && activePicker.label === label}
                onClick={() => handleSwatchClick("node", label)}
              />
            ))}
          </div>
        </div>

        {/* Divider */}
        <div className="w-px bg-border shrink-0" />

        {/* Edges column */}
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-widest font-semibold text-text-muted mb-2">
            Edges
          </div>
          <div className="space-y-1">
            {edgeLabels.map((label) => (
              <ColorItem
                key={label}
                label={label}
                color={getEdgeColor(label)}
                isActive={activePicker?.type === "edge" && activePicker.label === label}
                onClick={() => handleSwatchClick("edge", label)}
              />
            ))}
          </div>
        </div>
      </div>

      {/* Shade palette — appears below the columns when a swatch is clicked */}
      {activePicker && (
        <div className="mt-3 pt-3 border-t border-border">
          <div className="text-[10px] text-text-muted mb-2">
            Pick color for <span className="text-text-primary font-semibold">{activePicker.label}</span>
          </div>
          <div className="flex gap-1">
            {PALETTE.map((column, ci) => (
              <div key={ci} className="flex flex-col gap-1">
                {column.map((color, ri) => (
                  <button
                    key={ri}
                    className="w-5 h-5 rounded-sm border border-black/20 hover:scale-125 hover:z-10 transition-transform cursor-pointer"
                    style={{ backgroundColor: color }}
                    onClick={() => handlePaletteSelect(color)}
                    title={color}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Single color item row — swatch square + label text. */
function ColorItem({
  label,
  color,
  isActive,
  onClick,
}: {
  label: string;
  color: string;
  isActive: boolean;
  onClick: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        className={`w-4 h-4 rounded-sm shrink-0 cursor-pointer border transition-all ${
          isActive
            ? "border-brand ring-2 ring-brand/30 scale-110"
            : "border-black/20 hover:scale-110"
        }`}
        style={{ backgroundColor: color }}
        onClick={onClick}
        title={`Change color for ${label}`}
      />
      <span className="text-[11px] text-text-secondary truncate">{label}</span>
    </div>
  );
}
