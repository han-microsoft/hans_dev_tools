/**
 * Vite configuration — standalone graph visualizer.
 *
 * Path alias: `@/` maps to `src/` for clean imports.
 * Dev server runs on port 4173 to avoid conflicts with other projects.
 */
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 4173,
  },
});
