/**
 * @module useNodeColor
 *
 * Node colour resolution hook — maps graph node type labels to
 * hex colour values for consistent visual encoding.
 *
 * Resolution order:
 *   1. `nodeColorOverride[label]` — user-customised colour
 *   2. `autoColor(label)` — deterministic hash into {@link COLOR_PALETTE}
 *
 * Returns a memoised callback `(label: string) => string` suitable
 * for use in canvas rendering loops without causing re-renders.
 *
 * @param nodeColorOverride — `Record<string, string>` of user overrides
 * @returns `(label: string) => hexColor`
 */
import { useCallback } from 'react';
import { COLOR_PALETTE } from '@/constants/graphConstants';

/**
 * Deterministic hash-to-palette colour for a label string.
 * Uses a simple DJB2-like hash to pick a stable palette index.
 */
function autoColor(label: string): string {
  let hash = 0;
  for (const ch of label) hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0;
  return COLOR_PALETTE[Math.abs(hash) % COLOR_PALETTE.length];
}

/**
 * Centralized color resolution hook for graph nodes.
 *
 * Resolution: userOverride[label] → autoColor(label)
 */
export function useNodeColor(nodeColorOverride: Record<string, string>) {
  return useCallback(
    (label: string) => nodeColorOverride[label] ?? autoColor(label),
    [nodeColorOverride],
  );
}
