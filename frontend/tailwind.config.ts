import type { Config } from "tailwindcss";

/**
 * A restrained instrument palette. The brightest colours in the system are
 * reserved for data; chrome stays quiet so the numbers carry the contrast.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        graphite: {
          950: "#07080a",
          900: "#0b0d10",
          850: "#101317",
          800: "#161a1f",
          700: "#1e242b",
          600: "#2a323b",
          500: "#3a444f",
        },
        ink: {
          DEFAULT: "#e9edf2",
          muted: "#8d97a3",
          faint: "#5d6670",
        },
        signal: {
          ok: "#2ee6a8",
          warn: "#ffb020",
          alert: "#ff4d5e",
          info: "#4da6ff",
          purple: "#c77dff",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      keyframes: {
        pulseDot: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.25" },
        },
      },
      animation: {
        "pulse-dot": "pulseDot 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
