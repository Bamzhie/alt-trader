import type { Config } from "tailwindcss";

// Colors are the theme.css CSS vars — never raw hexes in components.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        "bg-elevated": "var(--bg-elevated)",
        card: "var(--card)",
        "card-hover": "var(--card-hover)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        fg: "var(--fg)",
        muted: "var(--muted)",
        accent: "var(--accent)",
        long: "var(--long)",
        short: "var(--short)",
        "chart-grid": "var(--chart-grid)",
      },
    },
  },
  plugins: [],
} satisfies Config;
