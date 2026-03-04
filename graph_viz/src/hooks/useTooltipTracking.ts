/**
 * @module useTooltipTracking
 *
 * Mouse-position tooltip tracking hook — maintains tooltip state
 * (position + hovered entity) for the graph visualisation.
 *
 * Attaches a global `mousemove` listener to track the cursor position
 * in real time. When a node or edge hover callback fires, the tooltip
 * state is set to the current mouse coordinates plus the hovered entity.
 *
 * Generic over node type `N` and edge type `E`.
 *
 * @returns `{ tooltip, handleNodeHover, handleLinkHover }`
 */
import { useState, useCallback, useRef, useEffect } from 'react';

/** Tooltip position + optional hovered entities. */
interface TooltipState<N, E> {
  x: number;
  y: number;
  node?: N;
  edge?: E;
}

/** Return shape for callers. */
interface TooltipTrackingResult<N, E> {
  tooltip: TooltipState<N, E> | null;
  handleNodeHover: (node: N | null) => void;
  handleLinkHover: (edge: E | null) => void;
}

export function useTooltipTracking<N, E>(): TooltipTrackingResult<N, E> {
  const [tooltip, setTooltip] = useState<TooltipState<N, E> | null>(null);

  /* Track mouse position globally so tooltip appears at cursor. */
  const mousePos = useRef({ x: 0, y: 0 });
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      mousePos.current = { x: e.clientX, y: e.clientY };
    };
    window.addEventListener('mousemove', handler);
    return () => window.removeEventListener('mousemove', handler);
  }, []);

  /** Set tooltip to current mouse pos + hovered node (or clear). */
  const handleNodeHover = useCallback((node: N | null) => {
    if (node) {
      setTooltip({ x: mousePos.current.x, y: mousePos.current.y, node, edge: undefined });
    } else {
      setTooltip(null);
    }
  }, []);

  /** Set tooltip to current mouse pos + hovered edge (or clear). */
  const handleLinkHover = useCallback((edge: E | null) => {
    if (edge) {
      setTooltip({ x: mousePos.current.x, y: mousePos.current.y, edge, node: undefined });
    } else {
      setTooltip(null);
    }
  }, []);

  return { tooltip, handleNodeHover, handleLinkHover };
}
