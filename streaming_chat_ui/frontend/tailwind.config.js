/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: "var(--color-brand)",
        "brand-hover": "var(--color-brand-hover)",
        "neutral-bg1": "var(--color-bg-1)",
        "neutral-bg2": "var(--color-bg-2)",
        "neutral-bg3": "var(--color-bg-3)",
        "neutral-bg4": "var(--color-bg-4)",
        "text-primary": "var(--color-text-primary)",
        "text-secondary": "var(--color-text-secondary)",
        "text-muted": "var(--color-text-muted)",
        border: "var(--color-border)",
        "status-success": "var(--color-status-success)",
        "status-error": "var(--color-status-error)",
        "status-warning": "var(--color-status-warning)",
      },
      animation: {
        "pulse-dot": "pulse-dot 1.5s ease-in-out infinite",
      },
      keyframes: {
        "pulse-dot": {
          "0%, 100%": { opacity: "0.3", transform: "scale(0.8)" },
          "50%": { opacity: "1", transform: "scale(1)" },
        },
      },
    },
  },
  plugins: [],
};
