/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "hsl(var(--bg))",
        panel: "hsl(var(--panel))",
        "panel-2": "hsl(var(--panel-2))",
        line: "hsl(var(--line))",
        ink: "hsl(var(--ink))",
        "ink-2": "hsl(var(--ink-2))",
        "ink-3": "hsl(var(--ink-3))",
        brand: {
          DEFAULT: "hsl(var(--brand))",
          2: "hsl(var(--brand-2))",
        },
        good: "hsl(var(--good))",
        warn: "hsl(var(--warn))",
        bad: "hsl(var(--bad))",
        border: "hsl(var(--line))",
        background: "hsl(var(--bg))",
        foreground: "hsl(var(--ink))",
        card: {
          DEFAULT: "hsl(var(--panel))",
          foreground: "hsl(var(--ink))",
        },
        primary: {
          DEFAULT: "hsl(var(--brand))",
          foreground: "hsl(var(--ink))",
        },
        muted: {
          DEFAULT: "hsl(var(--panel-2))",
          foreground: "hsl(var(--ink-2))",
        },
      },
      borderRadius: {
        control: "8px",
        card: "12px",
      },
      fontFamily: {
        sans: [
          "Inter",
          "Segoe UI",
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
      },
      fontSize: {
        base: ["14px", "20px"],
      },
    },
  },
  plugins: [],
};
