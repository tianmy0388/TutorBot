import type { Config } from "tailwindcss";

/**
 * Colors are wired through CSS variables so the theme can be switched at
 * runtime by toggling ``<html data-theme="...">`` (see globals.css).
 *
 * The vars are space-separated RGB triplets (without the ``rgb()``
 * wrapper), which lets Tailwind's ``<alpha-value>`` placeholder wrap
 * them so opacity modifiers like ``text-fg/10`` work correctly.
 */
const withAlpha = (rgb: string) => `rgb(${rgb} / <alpha-value>)`;

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: withAlpha("var(--color-bg)"),
          subtle: withAlpha("var(--color-bg-subtle)"),
          panel: withAlpha("var(--color-bg-panel)"),
          card: withAlpha("var(--color-bg-card)"),
        },
        fg: {
          DEFAULT: withAlpha("var(--color-fg)"),
          muted: withAlpha("var(--color-fg-muted)"),
          subtle: withAlpha("var(--color-fg-subtle)"),
        },
        border: {
          DEFAULT: withAlpha("var(--color-border)"),
        },
        brand: {
          50: withAlpha("var(--color-brand-50)"),
          100: withAlpha("var(--color-brand-100)"),
          200: withAlpha("var(--color-brand-200)"),
          300: withAlpha("var(--color-brand-300)"),
          400: withAlpha("var(--color-brand-400)"),
          500: withAlpha("var(--color-brand-500)"),
          600: withAlpha("var(--color-brand-600)"),
          700: withAlpha("var(--color-brand-700)"),
          800: withAlpha("var(--color-brand-800)"),
          900: withAlpha("var(--color-brand-900)"),
        },
        accent: {
          DEFAULT: withAlpha("var(--color-accent)"),
          warm: withAlpha("var(--color-accent-warm)"),
          green: withAlpha("var(--color-accent-green)"),
        },
      },
      fontFamily: {
        sans: [
          "var(--font-body)",
          "PingFang SC",
          "Hiragino Sans GB",
          "Microsoft YaHei",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
        display: [
          "var(--font-display)",
          "Songti SC",
          "STSong",
          "SimSun",
          "ui-serif",
          "Georgia",
          "serif",
        ],
        mono: [
          "var(--font-mono)",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      animation: {
        "fade-in": "fadeIn 0.4s cubic-bezier(0.2, 0.6, 0.2, 1) both",
        "slide-up": "slideUp 0.45s cubic-bezier(0.2, 0.6, 0.2, 1) both",
        "slide-down": "slideDown 0.4s cubic-bezier(0.2, 0.6, 0.2, 1) both",
        "scale-in": "scaleIn 0.3s cubic-bezier(0.2, 0.6, 0.2, 1) both",
        "pulse-slow": "pulse 3s ease-in-out infinite",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideUp: {
          "0%": { transform: "translateY(8px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        slideDown: {
          "0%": { transform: "translateY(-8px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        scaleIn: {
          "0%": { transform: "scale(0.96)", opacity: "0" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
