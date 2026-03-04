# Codebase Viz — Pluggable Graph Visualization Backends

Two independent graph visualization backends that consume the same `topology.json` format. Pick one or use both with the built-in tab switcher.

## Architecture

```
src/
  constants/
    graphConstants.ts        ← shared colour palettes (both backends)
  types/
    graph.ts                 ← shared types: TopologyNode, TopologyEdge, TopologyMeta
  hooks/
    useTopology.ts           ← shared data fetcher (loads /topology.json)
    useNodeColor.ts          ← shared colour resolver (label → hex)
    usePausableSimulation.ts ← force-graph only (no coupling to reactflow)
    useTooltipTracking.ts    ← force-graph only (no coupling to reactflow)
  components/
    graph/                   ← BACKEND A: Force Graph (react-force-graph-2d)
    reactflow/               ← BACKEND B: React Flow (@xyflow/react + dagre)
```

**Zero cross-backend imports.** Neither backend references the other. Both depend only on the shared layer (`constants/`, `types/`, `hooks/`).

---

## Data Format

Both backends consume a `topology.json` in the `public/` directory:

```json
{
  "nodes": [
    {
      "id": "core-router-01",
      "label": "Router",
      "properties": { "vendor": "Cisco", "status": "active" }
    }
  ],
  "edges": [
    {
      "id": "e1",
      "source": "core-router-01",
      "target": "firewall-01",
      "label": "ROUTED",
      "properties": { "interface": "Gi0/1" }
    }
  ]
}
```

Also accepts `{ topology_nodes, topology_edges }` as keys (for backward compatibility).

---

## Using Both Backends (Tab Switcher)

This is the default. Run the project as-is:

```bash
npm install
npm run dev
```

The tab bar at the top lets you switch between Force Graph and React Flow. Selection persists to `localStorage`.

---

## Using Only the Force Graph Backend

### Files to copy

```
src/constants/graphConstants.ts
src/types/graph.ts
src/hooks/useTopology.ts
src/hooks/useNodeColor.ts
src/hooks/usePausableSimulation.ts
src/hooks/useTooltipTracking.ts
src/components/graph/          ← entire directory
```

### npm dependencies

```bash
npm install react-force-graph-2d framer-motion lucide-react
```

### Wiring it up

```tsx
import { GraphTopologyViewer } from './components/graph';

// Provide pixel dimensions — the viewer fills them entirely.
<GraphTopologyViewer width={1200} height={800} />
```

The viewer fetches `/topology.json` on mount. No props required beyond dimensions.

### What you get

- Force-directed physics simulation (d3-force)
- Custom canvas node rendering (circles + labels)
- Node type filter toolbar with colour chips
- Edge type filter toolbar
- Search (find node by ID/label/property)
- Colour editor (per-type node and edge colours)
- Label style controls (font size, colour, node scale, edge width)
- Right-click context menu (change display field)
- Hover tooltips with full property inspection
- Pause/resume/zoom-to-fit simulation controls
- All customisations persisted to `localStorage`

### Removing React Flow traces

Delete `src/components/reactflow/` and uninstall:

```bash
npm uninstall @xyflow/react @dagrejs/dagre
```

---

## Using Only the React Flow Backend

### Files to copy

```
src/constants/graphConstants.ts
src/types/graph.ts
src/hooks/useTopology.ts
src/hooks/useNodeColor.ts
src/components/reactflow/      ← entire directory
```

Note: `usePausableSimulation.ts` and `useTooltipTracking.ts` are **not needed** — they're force-graph-specific.

### npm dependencies

```bash
npm install @xyflow/react @dagrejs/dagre
```

### Wiring it up

```tsx
import { ReactFlowTopologyViewer } from './components/reactflow';

<ReactFlowTopologyViewer width={1200} height={800} />
```

### CSS required

Add to your global CSS (or import `@xyflow/react/dist/style.css` and add the overrides):

```css
/* React Flow edge animation */
@keyframes dash-flow {
  to { stroke-dashoffset: -9; }
}

/* Clean up default React Flow node styling */
.react-flow__node {
  box-shadow: none !important;
  border: none !important;
  background: transparent !important;
  padding: 0 !important;
}
.react-flow__handle { opacity: 0; }
.react-flow__attribution { display: none !important; }
```

### What you get

- Dagre hierarchical auto-layout (switchable: ↓TB, →LR, ↑BT, ←RL)
- Rich DOM card nodes (status dot, label badge, property list)
- Animated dashed edges with floating label badges
- Built-in MiniMap (colour-coded by node type)
- Built-in Controls (zoom in/out/fit)
- Dot grid background
- Node type filtering
- Drag nodes to reposition
- All nodes are real React components (fully stylable with CSS/Tailwind)

### Removing Force Graph traces

Delete `src/components/graph/`, `src/hooks/usePausableSimulation.ts`, `src/hooks/useTooltipTracking.ts`, and uninstall:

```bash
npm uninstall react-force-graph-2d framer-motion lucide-react
```

(`framer-motion` and `lucide-react` are only used by the force-graph toolbar/tooltip.)

---

## Theming

Both backends use CSS custom properties for colours. The theme tokens are defined in `src/index.css`:

```css
:root {
  --color-brand: #117865;
  --color-bg-1: #FFFFFF;
  --color-text-primary: #242424;
  --color-border-default: #E0E0E0;
  /* ... */
}
.dark {
  --color-bg-1: #1B1B1B;
  --color-text-primary: #F0F0F0;
  /* ... */
}
```

Both backends read these tokens via Tailwind classes (`text-text-primary`, `bg-neutral-bg1`, etc.) and via `getComputedStyle` for canvas rendering. To use a different theme, override these CSS variables.

If you're importing into a project that doesn't use this theme system, replace the Tailwind classes with your own or hardcode colours.

---

## Integration Checklist

When importing a backend into an existing React project:

1. **Copy the files** listed above into your `src/` directory
2. **Install npm deps** for the chosen backend
3. **Set up path alias** — both backends use `@/` as a path alias to `src/`. Add to your `tsconfig.json`:
   ```json
   { "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["src/*"] } } }
   ```
   And in your bundler config (Vite example):
   ```ts
   resolve: { alias: { "@": path.resolve(__dirname, "./src") } }
   ```
4. **Add CSS** — copy the theme tokens from `index.css` or adapt to your design system
5. **Serve `topology.json`** — place it in your `public/` directory (or modify `useTopology.ts` to fetch from your API)
6. **Render the component** — pass `width` and `height` props

### Adapting the data source

If your data comes from an API instead of a static file, edit `useTopology.ts`:

```ts
// Change this line:
const res = await fetch('/topology.json', { signal: ctrl.signal });

// To your API endpoint:
const res = await fetch('/api/graph/topology', {
  signal: ctrl.signal,
  headers: { Authorization: `Bearer ${token}` },
});
```

The rest of the hook handles normalisation, error states, and abort-on-refetch automatically.
