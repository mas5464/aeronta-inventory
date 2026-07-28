/**
 * apps/site — parent-brand (aeronta.com) Tailwind theme.
 *
 * Deliberately does NOT use packages/tailwind-preset: Airvoyant is the
 * app's identity, not the marketing site's. Tokens live in
 * src/styles/brand.css. The `primary` / `muted` / `bad` keys and the
 * default border color are compat aliases that keep the pre-redesign
 * pages (product/pricing/docs/security/contact + ContactForm) compiling.
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./src/**/*.{astro,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // `<alpha-value>` keeps Tailwind opacity modifiers working
        // (bg-background/90, bg-sun/20, bg-background/5 are all used).
        background: "hsl(var(--background) / <alpha-value>)",
        foreground: "hsl(var(--foreground) / <alpha-value>)",
        border: "hsl(var(--border) / <alpha-value>)",
        muted: {
          DEFAULT: "hsl(var(--muted) / <alpha-value>)",
          foreground: "hsl(var(--muted-foreground) / <alpha-value>)",
        },
        coral: "hsl(var(--coral) / <alpha-value>)",
        peach: "hsl(var(--peach) / <alpha-value>)",
        cream: "hsl(var(--cream) / <alpha-value>)",
        forest: "hsl(var(--forest) / <alpha-value>)",
        mint: "hsl(var(--mint) / <alpha-value>)",
        sun: "hsl(var(--sun) / <alpha-value>)",
        panel: {
          DEFAULT: "hsl(var(--panel) / <alpha-value>)",
          line: "hsl(var(--panel-line) / <alpha-value>)",
          muted: "hsl(var(--panel-muted) / <alpha-value>)",
        },
        primary: {
          DEFAULT: "hsl(var(--foreground) / <alpha-value>)",
          foreground: "hsl(var(--background) / <alpha-value>)",
        },
        bad: "hsl(var(--coral) / <alpha-value>)",
      },
      borderColor: {
        DEFAULT: "hsl(var(--border))",
      },
      borderRadius: {
        card: "var(--radius)",
      },
      fontFamily: {
        sans: ['"Instrument Sans Variable"', "system-ui", "sans-serif"],
      },
      letterSpacing: {
        headline: "-0.04em",
      },
    },
  },
};
