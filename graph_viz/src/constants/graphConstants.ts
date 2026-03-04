/**
 * @module graphConstants
 *
 * Shared constants for graph visualisation — colour palettes used by
 * node and edge type rendering across all graph components.
 *
 * `COLOR_PALETTE` provides the default node type colours (12 vibrant hues).
 * `EDGE_COLOR_PALETTE` provides a softer, muted palette for edge types.
 */
export const COLOR_PALETTE = [
  '#38BDF8', '#FB923C', '#A78BFA', '#3B82F6',
  '#C084FC', '#CA8A04', '#FB7185', '#F472B6',
  '#10B981', '#EF4444', '#6366F1', '#FBBF24',
];

/** Dedicated palette for edge type chips (softer / muted tones). */
export const EDGE_COLOR_PALETTE = [
  '#64748B', '#94A3B8', '#78716C', '#6B7280',
  '#7C9CBF', '#A1887F', '#9E9E9E', '#8D6E63',
  '#80CBC4', '#B0BEC5', '#90A4AE', '#A5D6A7',
];
