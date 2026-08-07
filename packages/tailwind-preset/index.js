/** @type {import('tailwindcss').Config} */
module.exports = {
  theme: {
    extend: {
      colors: {
        /* Aeronta Design System Colors */
        bg: "hsl(var(--bg))",
        "bg-secondary": "hsl(var(--bg-secondary))",
        surface: "hsl(var(--surface))",
        border: "hsl(var(--border))",
        text: "hsl(var(--text))",
        "text-muted": "hsl(var(--text-muted))",
        "text-secondary": "hsl(var(--text-secondary))",

        /* Status Colors (Semantic) */
        success: "hsl(var(--success))",
        warning: "hsl(var(--warning))",
        error: "hsl(var(--error))",
        info: "hsl(var(--info))",
        danger: "hsl(var(--danger))",

        /* Data Visualization Palette */
        "series-1": "hsl(var(--series-1))",
        "series-2": "hsl(var(--series-2))",
        "series-3": "hsl(var(--series-3))",

        /* Brand Colors */
        brand: {
          DEFAULT: "hsl(var(--brand))",
          alt: "hsl(var(--brand-alt))",
        },

        /* Focus ring (parent-app blue) */
        ring: "hsl(var(--ring))",

        /* Accent (cream) — selected/hover fills, notices */
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },

        /* Signature accents (Aeronta parent palette) */
        forest: {
          DEFAULT: "hsl(var(--forest))",
          foreground: "hsl(var(--forest-foreground))",
        },
        cream: "hsl(var(--cream))",
        peach: "hsl(var(--peach))",
        mint: "hsl(var(--mint))",
        sun: "hsl(var(--sun))",

        /* Backwards Compatibility */
        panel: "hsl(var(--bg-secondary))",
        "panel-2": "hsl(var(--surface))",
        line: "hsl(var(--border))",
        ink: "hsl(var(--text))",
        "ink-2": "hsl(var(--text-muted))",
        "ink-3": "hsl(var(--text-secondary))",
        good: "hsl(var(--success))",
        warn: "hsl(var(--warning))",
        bad: "hsl(var(--error))",
        background: "hsl(var(--bg))",
        foreground: "hsl(var(--text))",
        card: {
          DEFAULT: "hsl(var(--bg-secondary))",
          foreground: "hsl(var(--text))",
        },
        primary: {
          DEFAULT: "hsl(var(--text))",
          foreground: "hsl(var(--bg))",
        },
        muted: {
          DEFAULT: "hsl(var(--surface))",
          foreground: "hsl(var(--text-muted))",
        },
      },
      spacing: {
        xs: "var(--space-xs)",
        sm: "var(--space-sm)",
        md: "var(--space-md)",
        lg: "var(--space-lg)",
        xl: "var(--space-xl)",
        "2xl": "var(--space-2xl)",
      },
      fontSize: {
        xs: ["var(--font-size-xs)", { lineHeight: "var(--line-height-tight)" }],
        sm: ["var(--font-size-sm)", { lineHeight: "var(--line-height-normal)" }],
        base: ["var(--font-size-base)", { lineHeight: "var(--line-height-normal)" }],
        lg: ["var(--font-size-lg)", { lineHeight: "var(--line-height-normal)" }],
        xl: ["var(--font-size-xl)", { lineHeight: "var(--line-height-tight)" }],
        "2xl": ["var(--font-size-2xl)", { lineHeight: "var(--line-height-tight)" }],
      },
      lineHeight: {
        tight: "var(--line-height-tight)",
        normal: "var(--line-height-normal)",
        relaxed: "var(--line-height-relaxed)",
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        full: "var(--radius-full)",
        control: "var(--radius-md)",
        card: "var(--radius-lg)",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        xl: "var(--shadow-xl)",
      },
      transitionDuration: {
        fast: "var(--transition-fast)",
        normal: "var(--transition-normal)",
        slow: "var(--transition-slow)",
      },
      fontFamily: {
        sans: [
          "Instrument Sans Variable",
          "Instrument Sans",
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
};
