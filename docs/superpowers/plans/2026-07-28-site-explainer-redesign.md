# apps/site Explainer Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the `apps/site` homepage as a parent-brand (aeronta.com) interactive explainer of the Aeronta Inventory business, with two React islands (WorkbenchDemo, SavingsEstimator) and a restyled site shell.

**Architecture:** `apps/site` is a static Astro 5 site (React islands via `@astrojs/react`, Tailwind 3) that deploys to its own Vercel project. This plan swaps its design tokens from the shared Airvoyant preset to a site-local parent-brand token file, restyles the shared `Base.astro` shell (which re-skins all six pages), and rebuilds `index.astro` as a 10-section story with two interactive islands. All CTAs route to the app at `PUBLIC_APP_URL`.

**Tech Stack:** Astro 5, React 18, Tailwind 3, Vitest + Testing Library, `@fontsource-variable/instrument-sans`, `@supabase/supabase-js` (build-time pricing reads, existing).

**Spec:** `docs/superpowers/specs/2026-07-28-site-explainer-redesign-design.md` — read it before starting.

## Global Constraints

- **Scope:** `apps/site` only (plus one `.claude/launch.json` entry and ROADMAP/TASKS bookkeeping). `apps/web`, `packages/tailwind-preset`, the BFF, and Supabase schema are **untouched**.
- **Brand tokens (exact, from aeronta.com):** ink/foreground `hsl(218 23% 12%)`, muted ink `hsl(218 8% 35%)`, border `hsl(0 0% 87%)`, coral `hsl(16 100% 33%)`, peach `hsl(24 95% 73%)`, cream `hsl(38 62% 90%)`, forest `hsl(126 64% 11%)`, mint `hsl(155 39% 75%)`, yellow `hsl(47 87% 66%)`, radius `0.75rem`. Light-only; **no theme toggle**.
- **Font:** Instrument Sans Variable, self-hosted via `@fontsource-variable/instrument-sans`. **No CDN font requests.**
- **Honesty rules (hard):** demo panel labeled "TRAX eMRO · synthetic demo"; estimator labeled illustrative with assumptions visible; no invented customers/logos/testimonials/point-estimate savings claims; pricing dollar figures only from the Supabase mirror (`getPricingTiers()`).
- **Motion:** IntersectionObserver + CSS reveals only; no GSAP; `prefers-reduced-motion` fully honored; page must read completely with JS disabled (`<noscript>` fallback).
- **App links:** `const appUrl = import.meta.env.PUBLIC_APP_URL ?? "https://aeronta-inventory.vercel.app"`; sign-in → `appUrl`, trial → `` `${appUrl}/#/signup?plan=growth` `` (or `?plan=${t.tier}` on tier cards).
- **Commands** (run from `apps/site/`): test `npm test`, build `npm run build`, dev `npm run dev`. There is no eslint config in `apps/site` — build + tests are the gate.
- Existing pages (product/pricing/docs/security/contact) keep their content; the new Tailwind config must keep their classes compiling via compat aliases (`primary`, `muted-foreground`, `bad`, default border color).

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `apps/site/package.json` | Modify | add `@fontsource-variable/instrument-sans` |
| `apps/site/src/styles/brand.css` | Create | parent-brand CSS custom properties + reveal CSS (the only token source for the site) |
| `apps/site/tailwind.config.mjs` | Modify | local theme mapping brand.css vars; drops the shared preset; compat aliases |
| `apps/site/src/layouts/Base.astro` | Modify | parent-brand shell: fonts, nav, footer, reveal script, `contained` prop |
| `apps/site/src/lib/estimator.ts` | Create | pure savings-band math + assumption constants + USD formatting |
| `apps/site/src/lib/estimator.test.ts` | Create | unit tests for the math |
| `apps/site/src/lib/tierFormat.ts` | Create | shared tier price formatter (used by pricing page + homepage teaser) |
| `apps/site/src/components/SavingsEstimator.tsx` | Create | sliders → illustrative band island |
| `apps/site/src/components/SavingsEstimator.test.tsx` | Create | island behavior tests |
| `apps/site/src/components/WorkbenchDemo.tsx` | Create | synthetic approve-a-recommendation hero island |
| `apps/site/src/components/WorkbenchDemo.test.tsx` | Create | island behavior tests |
| `apps/site/src/pages/index.astro` | Modify (full rebuild) | the 10-section explainer |
| `apps/site/src/pages/pricing.astro` | Modify (small) | use shared `tierFormat.ts` |
| `.claude/launch.json` | Modify | add `site-dev` config for browser QA |
| `ROADMAP.md`, `TASKS.md` | Modify | bookkeeping per repo Section C rules |

Unchanged but affected via the shell restyle (verify in Task 6): `product.astro`, `docs.mdx`, `security.astro`, `contact.astro`, `ContactForm.tsx`.

---

### Task 1: Parent-brand foundation (tokens, Tailwind theme, site shell)

**Files:**
- Modify: `apps/site/package.json` (via `npm install`)
- Create: `apps/site/src/styles/brand.css`
- Modify: `apps/site/tailwind.config.mjs` (full rewrite, 24 lines currently 8)
- Modify: `apps/site/src/layouts/Base.astro` (full rewrite)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: Tailwind color classes used by every later task — `bg-background`, `text-foreground`, `text-muted-foreground`, `bg-muted`, `border` (default color), `text-coral`, `bg-peach`, `bg-cream`, `bg-mint`, `bg-panel`, `text-panel-muted`, `border-panel-line`, `bg-primary text-primary-foreground`, `text-bad`, `rounded-card`, `tracking-headline`, `font-sans`. Also the `Base.astro` props contract: `title?: string`, `description?: string`, `contained?: boolean` (default `true`; homepage passes `false` for full-bleed sections), and the global reveal contract: any element with `data-reveal` fades/rises in when scrolled into view.

- [ ] **Step 1: Install the font package**

```bash
cd apps/site && npm install @fontsource-variable/instrument-sans
```

Expected: `package.json` gains `"@fontsource-variable/instrument-sans"` in `dependencies`; lockfile updates.

- [ ] **Step 2: Create the brand token file**

Create `apps/site/src/styles/brand.css`:

```css
/*
 * apps/site parent-brand tokens — mirrored from https://aeronta.com
 * (extracted live 2026-07-28). Light-only; HSL triplets (no hsl() wrapper)
 * for Tailwind composition in tailwind.config.mjs.
 *
 * This file REPLACES the shared Airvoyant preset for the marketing site.
 * apps/web keeps its own dark identity — do not import
 * packages/tailwind-preset/tokens.css here.
 */
:root {
  --background: 0 0% 100%;
  --foreground: 218 23% 12%; /* ink — text, dark panels, primary buttons */
  --muted: 210 40% 98%;
  --muted-foreground: 218 8% 35%;
  --border: 0 0% 87%;

  /* Signature accents (parent site) */
  --coral: 16 100% 33%;
  --peach: 24 95% 73%;
  --cream: 38 62% 90%;
  --forest: 126 64% 11%;
  --mint: 155 39% 75%;
  --sun: 47 87% 66%;

  /* Dark dashboard-mockup panels */
  --panel: 218 23% 12%;
  --panel-line: 218 15% 24%;
  --panel-muted: 218 10% 65%;

  --radius: 0.75rem;
}

/*
 * Scroll-reveal. Base.astro's IntersectionObserver adds .is-visible.
 * Reduced-motion users and no-JS visitors (noscript fallback in Base.astro)
 * see everything immediately.
 */
[data-reveal] {
  opacity: 0;
  transform: translateY(14px);
  transition:
    opacity 0.6s ease-out,
    transform 0.6s ease-out;
}
[data-reveal].is-visible {
  opacity: 1;
  transform: none;
}
@media (prefers-reduced-motion: reduce) {
  [data-reveal] {
    opacity: 1;
    transform: none;
    transition: none;
  }
}
```

- [ ] **Step 3: Rewrite the Tailwind config**

Replace the entire contents of `apps/site/tailwind.config.mjs` (it currently imports `packages/tailwind-preset` — that import must be gone):

```js
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
        DEFAULT: "hsl(var(--border) / <alpha-value>)",
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
```

- [ ] **Step 4: Rewrite the site shell**

Replace the entire contents of `apps/site/src/layouts/Base.astro`:

```astro
---
// apps/site/src/layouts/Base.astro — parent-brand (aeronta.com) shell.
// Tokens: src/styles/brand.css (light-only). Font: Instrument Sans Variable,
// self-hosted via @fontsource-variable — no CDN requests.
//
// `contained` (default true) wraps <main> in the standard page container;
// the homepage passes contained={false} to compose full-bleed sections.
import "@fontsource-variable/instrument-sans";
import "../styles/brand.css";

const {
  title = "Aeronta Inventory",
  description = "AI inventory optimization for airline spares.",
  contained = true,
} = Astro.props;
const appUrl = import.meta.env.PUBLIC_APP_URL ?? "https://aeronta-inventory.vercel.app";
---
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width" />
    <title>{title}</title>
    <meta name="description" content={description} />
    <meta property="og:title" content={title} />
    <meta property="og:description" content={description} />
    <noscript>
      <style>
        [data-reveal] {
          opacity: 1 !important;
          transform: none !important;
        }
      </style>
    </noscript>
  </head>
  <body class="min-h-screen bg-background font-sans text-foreground antialiased">
    <header class="sticky top-0 z-40 border-b bg-background/90 backdrop-blur">
      <div class="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
        <a href="/" class="flex items-center gap-2.5 font-semibold tracking-tight">
          <span class="grid h-8 w-8 place-items-center rounded-lg bg-panel text-sm font-bold text-background">A°</span>
          Aeronta Inventory
        </a>
        <nav class="flex items-center gap-5 text-sm">
          <a href="/product" class="hidden text-muted-foreground transition-colors hover:text-foreground sm:inline">Product</a>
          <a href="/pricing" class="hidden text-muted-foreground transition-colors hover:text-foreground sm:inline">Pricing</a>
          <a href="/docs" class="hidden text-muted-foreground transition-colors hover:text-foreground md:inline">Docs</a>
          <a href="/security" class="hidden text-muted-foreground transition-colors hover:text-foreground md:inline">Security</a>
          <a href="/contact" class="hidden text-muted-foreground transition-colors hover:text-foreground md:inline">Contact</a>
          <a href={appUrl} class="font-medium transition-colors hover:text-coral">Sign in</a>
          <a
            href={`${appUrl}/#/signup?plan=growth`}
            class="rounded-full bg-primary px-4 py-2 font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >Start free trial</a>
        </nav>
      </div>
    </header>
    <main class={contained ? "mx-auto max-w-6xl px-6 py-12" : ""}><slot /></main>
    <footer class="border-t px-6 py-10">
      <div class="mx-auto flex max-w-6xl flex-col justify-between gap-4 text-sm text-muted-foreground sm:flex-row">
        <span>© Aeronta Inventory</span>
        <div class="flex gap-5">
          <a href="/product" class="transition-colors hover:text-foreground">Product</a>
          <a href="/pricing" class="transition-colors hover:text-foreground">Pricing</a>
          <a href="/security" class="transition-colors hover:text-foreground">Security</a>
          <a href="/contact" class="transition-colors hover:text-foreground">Contact</a>
        </div>
      </div>
    </footer>
    <script>
      // Scroll-reveal: brand.css hides [data-reveal] and shows .is-visible.
      // Under prefers-reduced-motion the CSS shows everything immediately,
      // so this observer is a visual no-op there.
      const els = document.querySelectorAll("[data-reveal]");
      if ("IntersectionObserver" in window) {
        const io = new IntersectionObserver(
          (entries) => {
            for (const e of entries) {
              if (e.isIntersecting) {
                e.target.classList.add("is-visible");
                io.unobserve(e.target);
              }
            }
          },
          { threshold: 0.15 },
        );
        els.forEach((el) => io.observe(el));
      } else {
        els.forEach((el) => el.classList.add("is-visible"));
      }
    </script>
  </body>
</html>
```

- [ ] **Step 5: Verify the build and existing tests**

```bash
cd apps/site && npm run build && npm test
```

Expected: `astro build` completes (pricing degrades to Enterprise-only without env — that's normal); Vitest passes (ContactForm tests — 0 failures). If the build errors on the font import, check the package name is exactly `@fontsource-variable/instrument-sans`.

- [ ] **Step 6: Commit**

```bash
git add apps/site/package.json apps/site/package-lock.json apps/site/src/styles/brand.css apps/site/tailwind.config.mjs apps/site/src/layouts/Base.astro
git commit -m "feat(site): parent-brand foundation — tokens, Tailwind theme, restyled shell"
```

---

### Task 2: Estimator math library (TDD)

**Files:**
- Create: `apps/site/src/lib/estimator.ts`
- Test: `apps/site/src/lib/estimator.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces (used by Task 3 and Task 5):
  - `ASSUMPTIONS: { holdingRateLow: 0.18; holdingRateHigh: 0.25; reductionLow: 0.08; reductionHigh: 0.15 }`
  - `type SavingsBand = { low: number; high: number }`
  - `estimateSavings(onHandValueUsd: number): SavingsBand`
  - `perKey(band: SavingsBand, keys: number): SavingsBand`
  - `formatUsd(n: number): string` — e.g. `formatUsd(144000) === "$144,000"`
  - `pct(x: number): number` — e.g. `pct(0.18) === 18` (integer, no float dust)

- [ ] **Step 1: Write the failing tests**

Create `apps/site/src/lib/estimator.test.ts`:

```ts
// apps/site/src/lib/estimator.test.ts
import { describe, expect, it } from "vitest";
import { ASSUMPTIONS, estimateSavings, formatUsd, pct, perKey } from "./estimator";

describe("estimateSavings", () => {
  it("returns the low×low / high×high band of reduction × holding rate", () => {
    const band = estimateSavings(10_000_000);
    expect(band.low).toBeCloseTo(10_000_000 * 0.08 * 0.18, 5); // 144,000
    expect(band.high).toBeCloseTo(10_000_000 * 0.15 * 0.25, 5); // 375,000
  });

  it("is linear in on-hand value", () => {
    const one = estimateSavings(1_000_000);
    const five = estimateSavings(5_000_000);
    expect(five.low).toBeCloseTo(one.low * 5, 5);
    expect(five.high).toBeCloseTo(one.high * 5, 5);
  });

  it("clamps negative input to a zero band", () => {
    expect(estimateSavings(-5)).toEqual({ low: 0, high: 0 });
  });

  it("keeps low <= high for any non-negative input", () => {
    for (const v of [0, 1, 1_000_000, 500_000_000]) {
      const band = estimateSavings(v);
      expect(band.low).toBeLessThanOrEqual(band.high);
    }
  });
});

describe("perKey", () => {
  it("divides the band by the key count", () => {
    const per = perKey({ low: 144_000, high: 375_000 }, 1_000);
    expect(per.low).toBeCloseTo(144, 5);
    expect(per.high).toBeCloseTo(375, 5);
  });

  it("returns a zero band for zero or negative keys", () => {
    expect(perKey({ low: 100, high: 200 }, 0)).toEqual({ low: 0, high: 0 });
    expect(perKey({ low: 100, high: 200 }, -3)).toEqual({ low: 0, high: 0 });
  });
});

describe("formatUsd", () => {
  it("formats whole dollars, no cents", () => {
    expect(formatUsd(144_000)).toBe("$144,000");
    expect(formatUsd(0)).toBe("$0");
  });

  it("rounds fractional dollars", () => {
    expect(formatUsd(1234.56)).toBe("$1,235");
  });
});

describe("pct", () => {
  it("converts a rate to an integer percentage without float dust", () => {
    expect(pct(ASSUMPTIONS.holdingRateLow)).toBe(18);
    expect(pct(ASSUMPTIONS.holdingRateHigh)).toBe(25);
    expect(pct(ASSUMPTIONS.reductionLow)).toBe(8);
    expect(pct(ASSUMPTIONS.reductionHigh)).toBe(15);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/site && npx vitest run src/lib/estimator.test.ts
```

Expected: FAIL — cannot resolve `./estimator`.

- [ ] **Step 3: Write the implementation**

Create `apps/site/src/lib/estimator.ts`:

```ts
// apps/site/src/lib/estimator.ts
//
// Pure math behind the homepage SavingsEstimator island. The output is an
// ILLUSTRATIVE BAND, never a point estimate — a real operator's number comes
// from the Business Value Report after governed changes are applied.
//
// Assumption set (rendered verbatim next to the sliders on the page):
//   - Annual holding cost runs 18–25% of on-hand inventory value
//     (industry-typical range for aviation spares).
//   - Governed optimization typically reduces excess on-hand value by 8–15%.
// Band = on-hand value × reduction × holding rate, pairing low×low and
// high×high so the band brackets the assumption space.

export const ASSUMPTIONS = {
  holdingRateLow: 0.18,
  holdingRateHigh: 0.25,
  reductionLow: 0.08,
  reductionHigh: 0.15,
} as const;

export type SavingsBand = { low: number; high: number };

export function estimateSavings(onHandValueUsd: number): SavingsBand {
  const v = Math.max(0, onHandValueUsd);
  return {
    low: v * ASSUMPTIONS.reductionLow * ASSUMPTIONS.holdingRateLow,
    high: v * ASSUMPTIONS.reductionHigh * ASSUMPTIONS.holdingRateHigh,
  };
}

export function perKey(band: SavingsBand, keys: number): SavingsBand {
  if (keys <= 0) return { low: 0, high: 0 };
  return { low: band.low / keys, high: band.high / keys };
}

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

export function formatUsd(n: number): string {
  return usd.format(n);
}

// 0.18 * 100 === 18.000000000000004 in IEEE 754 — round for display.
export function pct(x: number): number {
  return Math.round(x * 100);
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/site && npx vitest run src/lib/estimator.test.ts
```

Expected: PASS — 9 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/site/src/lib/estimator.ts apps/site/src/lib/estimator.test.ts
git commit -m "feat(site): savings estimator math — illustrative band from published assumptions"
```

---

### Task 3: SavingsEstimator island (TDD)

**Files:**
- Create: `apps/site/src/components/SavingsEstimator.tsx`
- Test: `apps/site/src/components/SavingsEstimator.test.tsx`

**Interfaces:**
- Consumes (Task 2): `ASSUMPTIONS`, `estimateSavings`, `perKey`, `formatUsd`, `pct` from `../lib/estimator`.
- Produces (Task 5): named export `SavingsEstimator` (no props), mounted as `<SavingsEstimator client:visible />`. Defaults: 25,000 keys, $50,000,000 on-hand → initial band "$720,000 – $1,875,000". Sliders carry `aria-label="Part-location keys"` and `aria-label="On-hand inventory value in dollars"`.

- [ ] **Step 1: Write the failing tests**

Create `apps/site/src/components/SavingsEstimator.test.tsx`:

```tsx
// apps/site/src/components/SavingsEstimator.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SavingsEstimator } from "./SavingsEstimator";

describe("SavingsEstimator", () => {
  it("renders the default band for 25k keys / $50M on-hand", () => {
    render(<SavingsEstimator />);
    // 50M × 0.08 × 0.18 = 720,000 ; 50M × 0.15 × 0.25 = 1,875,000
    expect(screen.getByTestId("savings-band")).toHaveTextContent("$720,000 – $1,875,000");
  });

  it("recomputes the band when the on-hand value slider moves", () => {
    render(<SavingsEstimator />);
    fireEvent.change(screen.getByLabelText("On-hand inventory value in dollars"), {
      target: { value: "10000000" },
    });
    // 10M × 0.08 × 0.18 = 144,000 ; 10M × 0.15 × 0.25 = 375,000
    expect(screen.getByTestId("savings-band")).toHaveTextContent("$144,000 – $375,000");
  });

  it("recomputes the per-key line when the keys slider moves", () => {
    render(<SavingsEstimator />);
    fireEvent.change(screen.getByLabelText("Part-location keys"), {
      target: { value: "10000" },
    });
    // 720,000 / 10,000 = 72 ; 1,875,000 / 10,000 ≈ 188 (rounded by formatUsd)
    expect(screen.getByTestId("per-key")).toHaveTextContent("$72–$188 per key");
  });

  it("shows the assumption set verbatim (honesty rule)", () => {
    render(<SavingsEstimator />);
    const note = screen.getByTestId("assumptions");
    expect(note).toHaveTextContent("18–25%");
    expect(note).toHaveTextContent("8–15%");
    expect(note).toHaveTextContent("Business Value Report");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/site && npx vitest run src/components/SavingsEstimator.test.tsx
```

Expected: FAIL — cannot resolve `./SavingsEstimator`.

- [ ] **Step 3: Write the implementation**

Create `apps/site/src/components/SavingsEstimator.tsx`:

```tsx
// apps/site/src/components/SavingsEstimator.tsx
//
// Homepage island (client:visible). Two sliders → an illustrative annual
// savings band computed by src/lib/estimator.ts. Astro server-renders the
// default state, so no-JS visitors still see a complete worked example and
// the assumption text; only the sliders need hydration.
import { useState } from "react";
import { ASSUMPTIONS, estimateSavings, formatUsd, pct, perKey } from "../lib/estimator";

const DEFAULT_KEYS = 25_000;
const DEFAULT_VALUE = 50_000_000;

export function SavingsEstimator() {
  const [keys, setKeys] = useState(DEFAULT_KEYS);
  const [value, setValue] = useState(DEFAULT_VALUE);
  const band = estimateSavings(value);
  const per = perKey(band, keys);

  return (
    <div className="rounded-card border bg-muted p-6 sm:p-8">
      <div className="grid gap-8 md:grid-cols-2">
        <div className="space-y-6">
          <label className="block">
            <span className="flex justify-between text-sm">
              <span className="font-medium">Part–location keys</span>
              <span className="text-muted-foreground">{keys.toLocaleString("en-US")}</span>
            </span>
            <input
              type="range"
              min={1_000}
              max={100_000}
              step={1_000}
              value={keys}
              onChange={(e) => setKeys(Number(e.target.value))}
              className="mt-2 w-full accent-coral"
              aria-label="Part-location keys"
            />
          </label>
          <label className="block">
            <span className="flex justify-between text-sm">
              <span className="font-medium">On-hand inventory value</span>
              <span className="text-muted-foreground">{formatUsd(value)}</span>
            </span>
            <input
              type="range"
              min={1_000_000}
              max={500_000_000}
              step={1_000_000}
              value={value}
              onChange={(e) => setValue(Number(e.target.value))}
              className="mt-2 w-full accent-coral"
              aria-label="On-hand inventory value in dollars"
            />
          </label>
          <p className="text-xs leading-relaxed text-muted-foreground" data-testid="assumptions">
            Illustrative model — annual holding cost at {pct(ASSUMPTIONS.holdingRateLow)}–
            {pct(ASSUMPTIONS.holdingRateHigh)}% of on-hand value, with governed optimization
            reducing excess on-hand value by {pct(ASSUMPTIONS.reductionLow)}–
            {pct(ASSUMPTIONS.reductionHigh)}%. Your real number comes from the Business Value
            Report, attributed to specific applied changes.
          </p>
        </div>
        <div className="flex flex-col justify-center rounded-card bg-panel p-6 text-background">
          <div className="text-xs uppercase tracking-wide text-panel-muted">
            Illustrative annual savings
          </div>
          <div className="mt-2 text-3xl font-medium tracking-headline" data-testid="savings-band">
            {formatUsd(band.low)} – {formatUsd(band.high)}
          </div>
          <div className="mt-2 text-sm text-panel-muted" data-testid="per-key">
            ≈ {formatUsd(per.low)}–{formatUsd(per.high)} per key across{" "}
            {keys.toLocaleString("en-US")} keys
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/site && npx vitest run src/components/SavingsEstimator.test.tsx
```

Expected: PASS — 4 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/site/src/components/SavingsEstimator.tsx apps/site/src/components/SavingsEstimator.test.tsx
git commit -m "feat(site): SavingsEstimator island — sliders to illustrative savings band"
```

---

### Task 4: WorkbenchDemo island (TDD)

**Files:**
- Create: `apps/site/src/components/WorkbenchDemo.tsx`
- Test: `apps/site/src/components/WorkbenchDemo.test.tsx`

**Interfaces:**
- Consumes (Task 2): `formatUsd` from `../lib/estimator`.
- Produces (Task 5): named export `WorkbenchDemo` (no props), mounted as `<WorkbenchDemo client:load />` in the hero. Dark panel labeled "TRAX eMRO · synthetic demo". One synthetic pending recommendation; **Approve** transitions it to written, appends an audit-ledger entry, and ticks a projected-savings counter to $9,120; **Reset** restores the initial state.

**Behavior notes for the implementer:**
- jsdom does not implement `window.matchMedia` — the component must treat "matchMedia unavailable" as reduced motion (counter jumps straight to the final value). This is also what makes the tests deterministic without faking `requestAnimationFrame`.
- The savings counter animates with `requestAnimationFrame` over ~800 ms only when motion is allowed.
- All data is hardcoded synthetic — that is the point; it is disclosed in the panel chrome exactly like the parent site does ("TRAX eMRO · synthetic demo").

- [ ] **Step 1: Write the failing tests**

Create `apps/site/src/components/WorkbenchDemo.test.tsx`:

```tsx
// apps/site/src/components/WorkbenchDemo.test.tsx
//
// jsdom has no matchMedia, so the component's reduced-motion fallback kicks
// in and the savings counter lands immediately — no rAF faking needed.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WorkbenchDemo } from "./WorkbenchDemo";

describe("WorkbenchDemo", () => {
  it("starts with a pending recommendation and the synthetic-demo disclosure", () => {
    render(<WorkbenchDemo />);
    expect(screen.getByText("TRAX eMRO · synthetic demo")).toBeInTheDocument();
    expect(screen.getByText("Pending approval")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(screen.queryByTestId("ledger-entry")).not.toBeInTheDocument();
    expect(screen.getByTestId("savings-counter")).toHaveTextContent("$0");
  });

  it("approve writes the change: status flips, ledger entry appears, counter lands", () => {
    render(<WorkbenchDemo />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(screen.getByText("Written to eMRO")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    const entry = screen.getByTestId("ledger-entry");
    expect(entry).toHaveTextContent("3290-45-11");
    expect(entry).toHaveTextContent("MIA");
    expect(entry).toHaveTextContent("ROP 6→3");
    expect(entry).toHaveTextContent("EOQ 12→5");
    expect(entry).toHaveTextContent("SS 4→2");
    expect(entry).toHaveTextContent("Max 18→8");
    expect(entry).toHaveTextContent("planner");
    // matchMedia unavailable → reduced-motion path → counter jumps to final.
    expect(screen.getByTestId("savings-counter")).toHaveTextContent("$9,120");
  });

  it("reset restores the pending state", () => {
    render(<WorkbenchDemo />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset demo" }));
    expect(screen.getByText("Pending approval")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(screen.queryByTestId("ledger-entry")).not.toBeInTheDocument();
    expect(screen.getByTestId("savings-counter")).toHaveTextContent("$0");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/site && npx vitest run src/components/WorkbenchDemo.test.tsx
```

Expected: FAIL — cannot resolve `./WorkbenchDemo`.

- [ ] **Step 3: Write the implementation**

Create `apps/site/src/components/WorkbenchDemo.tsx`:

```tsx
// apps/site/src/components/WorkbenchDemo.tsx
//
// Hero island (client:load): a synthetic approval-queue panel in the parent
// site's dark-mockup grammar. The visitor approves one governed
// recommendation and watches the write land — status flips, an append-only
// ledger entry appears with before/after values, and the projected-savings
// counter ticks up. Reset restarts it.
//
// Every value here is hardcoded synthetic data, disclosed in the panel
// chrome ("TRAX eMRO · synthetic demo") exactly like aeronta.com labels its
// own demo panels. No real tenant, part, or dollar figure appears.
import { useEffect, useRef, useState } from "react";
import { formatUsd } from "../lib/estimator";

const REC = {
  pn: "3290-45-11",
  description: "Fuel shutoff valve",
  location: "MIA",
  tier: "B",
  current: { rop: 6, eoq: 12, ss: 4, max: 18 },
  recommended: { rop: 3, eoq: 5, ss: 2, max: 8 },
  annualSavingUsd: 9_120,
  reason:
    "24 months of demand support a lower reorder point — excess on-hand value is carrying avoidable holding cost.",
} as const;

// jsdom (tests) has no matchMedia; treat that as reduced motion so the
// counter is deterministic. Real browsers report the user's preference.
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const VALUE_ROWS = [
  { label: "ROP", from: REC.current.rop, to: REC.recommended.rop },
  { label: "EOQ", from: REC.current.eoq, to: REC.recommended.eoq },
  { label: "SS", from: REC.current.ss, to: REC.recommended.ss },
  { label: "Max", from: REC.current.max, to: REC.recommended.max },
] as const;

export function WorkbenchDemo() {
  const [written, setWritten] = useState(false);
  const [savings, setSavings] = useState(0);
  const rafRef = useRef(0);

  useEffect(() => () => cancelAnimationFrame(rafRef.current), []);

  function approve() {
    setWritten(true);
    if (prefersReducedMotion()) {
      setSavings(REC.annualSavingUsd);
      return;
    }
    const start = performance.now();
    const DURATION = 800;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / DURATION);
      setSavings(Math.round(REC.annualSavingUsd * t));
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }

  function reset() {
    cancelAnimationFrame(rafRef.current);
    setWritten(false);
    setSavings(0);
  }

  return (
    <div className="overflow-hidden rounded-card bg-panel text-background shadow-xl">
      <div className="flex items-center justify-between border-b border-panel-line px-5 py-3 text-xs">
        <span className="font-medium">Demo Air · Materials planning</span>
        <span className="text-panel-muted">TRAX eMRO · synthetic demo</span>
      </div>

      <div className="space-y-4 p-5">
        <div className="rounded-lg border border-panel-line p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="font-mono text-sm">{REC.pn}</div>
              <div className="text-xs text-panel-muted">
                {REC.description} · {REC.location}
              </div>
            </div>
            {written ? (
              <span className="rounded-full bg-mint px-2.5 py-1 text-xs font-medium text-forest">
                Written to eMRO
              </span>
            ) : (
              <span className="rounded-full bg-sun/20 px-2.5 py-1 text-xs font-medium text-sun">
                Pending approval
              </span>
            )}
          </div>

          <dl className="mt-4 grid grid-cols-4 gap-2 text-center">
            {VALUE_ROWS.map((row) => (
              <div key={row.label} className="rounded-md bg-background/5 p-2">
                <dt className="text-[10px] uppercase tracking-wide text-panel-muted">
                  {row.label}
                </dt>
                <dd className="mt-1 text-sm">
                  <span className="text-panel-muted line-through">{row.from}</span>{" "}
                  <span className="font-medium text-peach">{row.to}</span>
                </dd>
              </div>
            ))}
          </dl>

          <p className="mt-3 text-xs leading-relaxed text-panel-muted">{REC.reason}</p>

          <div className="mt-4 flex items-center justify-between gap-3">
            <div className="text-xs text-panel-muted">
              Tier {REC.tier} · projected {formatUsd(REC.annualSavingUsd)}/yr
            </div>
            {written ? (
              <button
                type="button"
                onClick={reset}
                className="text-xs text-panel-muted underline-offset-2 hover:underline"
              >
                Reset demo
              </button>
            ) : (
              <button
                type="button"
                onClick={approve}
                className="rounded-full bg-peach px-4 py-1.5 text-xs font-semibold text-panel transition-opacity hover:opacity-90"
              >
                Approve
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between rounded-lg border border-panel-line px-4 py-3">
          <span className="text-xs text-panel-muted">Projected annual savings unlocked</span>
          <span className="text-lg font-medium text-peach" data-testid="savings-counter">
            {formatUsd(savings)}
          </span>
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-wide text-panel-muted">
            Audit ledger · append-only
          </div>
          {written ? (
            <div
              className="mt-2 rounded-lg border border-panel-line px-4 py-3 text-xs leading-relaxed"
              data-testid="ledger-entry"
            >
              <span className="font-mono">{REC.pn}</span> @ {REC.location} — ROP{" "}
              {REC.current.rop}→{REC.recommended.rop} · EOQ {REC.current.eoq}→
              {REC.recommended.eoq} · SS {REC.current.ss}→{REC.recommended.ss} · Max{" "}
              {REC.current.max}→{REC.recommended.max}
              <div className="mt-1 text-panel-muted">
                principal: planner · rollback available · just now
              </div>
            </div>
          ) : (
            <p className="mt-2 text-xs text-panel-muted">
              Approve the recommendation to see the write land here — with before/after
              values and a rollback path.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/site && npx vitest run src/components/WorkbenchDemo.test.tsx
```

Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add apps/site/src/components/WorkbenchDemo.tsx apps/site/src/components/WorkbenchDemo.test.tsx
git commit -m "feat(site): WorkbenchDemo island — approve a synthetic governed write"
```

---

### Task 5: Homepage rebuild + shared tier formatter

**Files:**
- Create: `apps/site/src/lib/tierFormat.ts`
- Modify: `apps/site/src/pages/pricing.astro:13-17` (replace the inline `fmt` with the shared formatter)
- Modify: `apps/site/src/pages/index.astro` (full rebuild)

**Interfaces:**
- Consumes: `WorkbenchDemo` (Task 4), `SavingsEstimator` (Task 3), `Base` with `contained={false}` and `data-reveal` (Task 1), `getPricingTiers()` + `Tier` from `../lib/supabase` (existing).
- Produces: `formatTierPrice(amount: number | null, currency: string | null): string` in `tierFormat.ts` — e.g. `formatTierPrice(149900, "usd") === "$1,499/mo"`, `formatTierPrice(null, null) === "Contact us"`.

- [ ] **Step 1: Create the shared tier price formatter**

Create `apps/site/src/lib/tierFormat.ts` (extracted verbatim from the logic currently inlined in `pricing.astro` so the homepage teaser and the pricing page can't drift):

```ts
// apps/site/src/lib/tierFormat.ts
//
// One formatter for tier prices, shared by pricing.astro and the homepage
// pricing teaser. Amounts come from the Supabase Stripe mirror in cents;
// null means "no self-serve price" (Enterprise / mirror unavailable).
export function formatTierPrice(amount: number | null, currency: string | null): string {
  if (amount == null) return "Contact us";
  const symbol = currency === "usd" ? "$" : currency ? `${currency.toUpperCase()} ` : "";
  return `${symbol}${(amount / 100).toLocaleString()}/mo`;
}
```

- [ ] **Step 2: Point pricing.astro at the shared formatter**

In `apps/site/src/pages/pricing.astro`, replace the frontmatter lines

```ts
const fmt = (amount: number | null, currency: string | null) => {
  if (amount == null) return "Contact us";
  const symbol = currency === "usd" ? "$" : currency ? `${currency.toUpperCase()} ` : "";
  return `${symbol}${(amount / 100).toLocaleString()}/mo`;
};
```

with

```ts
import { formatTierPrice } from "../lib/tierFormat";
```

(move the import up with the other imports), and change the one call site `{fmt(t.unit_amount, t.currency)}` to `{formatTierPrice(t.unit_amount, t.currency)}`.

- [ ] **Step 3: Rebuild the homepage**

Replace the entire contents of `apps/site/src/pages/index.astro`:

```astro
---
// apps/site/src/pages/index.astro — the interactive explainer homepage.
// Full-bleed composition (Base contained={false}); every displayed dollar
// figure is either labeled synthetic/illustrative or comes from the
// Supabase pricing mirror. Spec:
// docs/superpowers/specs/2026-07-28-site-explainer-redesign-design.md
import Base from "../layouts/Base.astro";
import { WorkbenchDemo } from "../components/WorkbenchDemo";
import { SavingsEstimator } from "../components/SavingsEstimator";
import { getPricingTiers } from "../lib/supabase";
import { formatTierPrice } from "../lib/tierFormat";

const appUrl = import.meta.env.PUBLIC_APP_URL ?? "https://aeronta-inventory.vercel.app";
const tiers = await getPricingTiers();

const steps = [
  {
    n: "01",
    name: "Recommend",
    lede: "Every number, regime-tuned and traceable.",
    body: "A forecasting layer matched to each demand regime — statistical models for steady consumption, gradient-boosted models for volume, empirical-Bayes shrinkage for ultra-rare parts — produces a recommended reorder point, order quantity, safety stock, and max for every part–location. Every number ships with its provenance: which model produced it, what data it saw, how fresh that data is.",
    accent: "bg-cream",
  },
  {
    n: "02",
    name: "Govern",
    lede: "Autonomy is tiered. Guardrails are not.",
    body: "Tiered autonomy decides how much latitude each change gets — auto-apply for low-risk, high-confidence moves; a planner approval queue for the rest. Hard guardrails are never bypassed regardless of tier: single-write deltas are capped, shelf-life and hazmat clamps always apply, and an active AOG forces the most conservative posture. A per-tenant kill switch halts all writes instantly.",
    accent: "bg-mint",
  },
  {
    n: "03",
    name: "Act",
    lede: "One write path. Every write reversible.",
    body: "Approved changes write back into your MRO inventory levels — the write-back component is the only part of the system with write permission; everything else is read-only by construction. Each write records before/after values with a rollback window, and a running Business Value Report attributes savings to the specific changes that produced them.",
    accent: "bg-peach",
  },
];

const bvrComponents = [
  {
    name: "Holding cost",
    body: "Excess on-hand value released by lower, governed stocking levels — valued at your holding rate, not a vendor's.",
  },
  {
    name: "Ordering cost",
    body: "Fewer, better-sized replenishment orders as EOQ tracks reality instead of a years-old snapshot.",
  },
  {
    name: "Stockout risk",
    body: "Reduced exposure on the parts that ground aircraft, priced against the service level you actually target.",
  },
];

const trust = [
  {
    name: "Guardrails never bypassed",
    body: "Delta caps, shelf-life and hazmat clamps, and AOG posture apply in every tier — including the most autonomous.",
  },
  {
    name: "Tenant isolation at every layer",
    body: "Row-level security in the database, tenant checks in the agent layer — isolation is architecture, not convention.",
  },
  {
    name: "Append-only audit ledger",
    body: "Every write and every planner decision is recorded with before/after values and the principal responsible. Nothing is edited after the fact.",
  },
  {
    name: "Rollback is never a one-way door",
    body: "Every applied change keeps its before-values and a rollback path over a configurable window.",
  },
];
---
<Base contained={false}>
  <!-- 2. Hero -->
  <section class="border-b">
    <div class="mx-auto grid max-w-6xl items-center gap-12 px-6 py-16 sm:py-24 lg:grid-cols-2">
      <div class="space-y-7">
        <div class="h-1 w-12 rounded-full bg-coral"></div>
        <h1 class="max-w-xl text-[2.6rem] font-[430] leading-[1.05] tracking-headline sm:text-6xl">
          Stop planning spares with numbers that went stale years ago.
        </h1>
        <p class="max-w-xl text-lg text-muted-foreground">
          Aeronta Inventory continuously recomputes reorder point, order quantity, safety
          stock, and max for every part at every station — and writes the result back into
          your MRO system under governed autonomy.
        </p>
        <div class="flex flex-wrap gap-3">
          <a
            href={`${appUrl}/#/signup?plan=growth`}
            class="rounded-full bg-primary px-6 py-3 font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >Start free trial</a>
          <a
            href={appUrl}
            class="rounded-full border px-6 py-3 font-medium transition-colors hover:border-foreground"
          >Sign in ↗</a>
        </div>
        <ul class="flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted-foreground">
          <li class="flex items-center gap-2"><span class="text-coral">✓</span> Governed write-back</li>
          <li class="flex items-center gap-2"><span class="text-coral">✓</span> Every number carries its provenance</li>
          <li class="flex items-center gap-2"><span class="text-coral">✓</span> Savings attributed in dollars</li>
        </ul>
      </div>
      <WorkbenchDemo client:load />
    </div>
  </section>

  <!-- 3. The problem -->
  <section class="border-b bg-muted">
    <div class="mx-auto max-w-6xl px-6 py-16 sm:py-24" data-reveal>
      <h2 class="max-w-2xl text-3xl font-[430] tracking-headline sm:text-4xl">
        Static min/max values decay. Quietly, and expensively.
      </h2>
      <p class="mt-6 max-w-2xl text-lg text-muted-foreground">
        Most airline inventories still run on reorder points set years ago — snapshots of a
        fleet, a network, and a vendor base that no longer exist. Demand for rotables is
        intermittent by nature, lead times swing with every vendor and shipping mode, and a
        shortage on the wrong part grounds an aircraft. So planners hedge: capital piles up
        on the shelf while the next AOG hides in the tail of the demand curve.
      </p>
      <div class="mt-10 grid gap-6 md:grid-cols-2">
        <div class="rounded-card border bg-background p-6">
          <div class="text-sm font-medium text-coral">Without governance</div>
          <div class="mt-2 text-xl font-[430] tracking-headline">Set once, drift forever.</div>
          <p class="mt-3 text-sm text-muted-foreground">
            Levels reviewed part-by-part, when someone has time, usually after the shortage
            or the write-off has already happened.
          </p>
        </div>
        <div class="rounded-card border bg-background p-6">
          <div class="text-sm font-medium text-forest">With Aeronta</div>
          <div class="mt-2 text-xl font-[430] tracking-headline">Recomputed continuously, governed always.</div>
          <p class="mt-3 text-sm text-muted-foreground">
            Every part–location recomputed on fresh demand and lead-time signal — and no
            change lands without clearing the autonomy policy you set.
          </p>
        </div>
      </div>
    </div>
  </section>

  <!-- 4. How it works -->
  <section class="border-b">
    <div class="mx-auto max-w-6xl px-6 py-16 sm:py-24">
      <h2 class="max-w-2xl text-3xl font-[430] tracking-headline sm:text-4xl" data-reveal>
        One governed loop: recommend, govern, act.
      </h2>
      <div class="mt-12 grid gap-8 lg:grid-cols-3">
        {steps.map((s) => (
          <div class="space-y-4" data-reveal>
            <div class={`inline-flex h-10 w-14 items-center justify-center rounded-full ${s.accent} text-sm font-semibold`}>
              {s.n}
            </div>
            <h3 class="text-xl font-semibold">{s.name}</h3>
            <div class="text-sm font-medium text-coral">{s.lede}</div>
            <p class="text-sm leading-relaxed text-muted-foreground">{s.body}</p>
          </div>
        ))}
      </div>
    </div>
  </section>

  <!-- 5. Savings estimator -->
  <section class="border-b bg-muted">
    <div class="mx-auto max-w-6xl px-6 py-16 sm:py-24" data-reveal>
      <h2 class="max-w-2xl text-3xl font-[430] tracking-headline sm:text-4xl">
        What does governed optimization return?
      </h2>
      <p class="mt-4 max-w-2xl text-muted-foreground">
        Move the sliders to your scale. The band below is an illustrative model with its
        assumptions in plain sight — not a quote, and not a promise.
      </p>
      <div class="mt-10">
        <SavingsEstimator client:visible />
      </div>
    </div>
  </section>

  <!-- 6. Proof: the BVR -->
  <section class="border-b">
    <div class="mx-auto max-w-6xl px-6 py-16 sm:py-24">
      <h2 class="max-w-2xl text-3xl font-[430] tracking-headline sm:text-4xl" data-reveal>
        Savings you can audit, not a dashboard you have to trust.
      </h2>
      <p class="mt-4 max-w-2xl text-muted-foreground" data-reveal>
        The Business Value Report attributes every dollar to specific applied changes,
        measured against the pre-agent baseline — and discloses exactly how much of your
        portfolio the valuation covers.
      </p>
      <div class="mt-10 grid gap-6 md:grid-cols-3">
        {bvrComponents.map((c) => (
          <div class="rounded-card border p-6" data-reveal>
            <h3 class="font-semibold">{c.name}</h3>
            <p class="mt-2 text-sm leading-relaxed text-muted-foreground">{c.body}</p>
          </div>
        ))}
      </div>
    </div>
  </section>

  <!-- 7. Trust strip -->
  <section class="border-b bg-muted">
    <div class="mx-auto max-w-6xl px-6 py-16 sm:py-24">
      <h2 class="max-w-2xl text-3xl font-[430] tracking-headline sm:text-4xl" data-reveal>
        Built to be trusted with the write.
      </h2>
      <div class="mt-10 grid gap-px overflow-hidden rounded-card border bg-border sm:grid-cols-2">
        {trust.map((t) => (
          <div class="bg-background p-6" data-reveal>
            <h3 class="font-semibold">{t.name}</h3>
            <p class="mt-2 text-sm leading-relaxed text-muted-foreground">{t.body}</p>
          </div>
        ))}
      </div>
      <p class="mt-6 text-sm text-muted-foreground" data-reveal>
        More on isolation, encryption, and the audit ledger on the
        <a href="/security" class="underline transition-colors hover:text-foreground">security page</a>.
      </p>
    </div>
  </section>

  <!-- 8. Pricing teaser -->
  <section class="border-b">
    <div class="mx-auto max-w-6xl px-6 py-16 sm:py-24">
      <h2 class="max-w-2xl text-3xl font-[430] tracking-headline sm:text-4xl" data-reveal>
        Priced by part–location keys.
      </h2>
      <p class="mt-4 max-w-2xl text-muted-foreground" data-reveal>
        Every self-serve tier includes a 14-day free trial — card required, cancel anytime.
      </p>
      {tiers.length > 0 ? (
        <div class="mt-10 grid gap-6 md:grid-cols-2 lg:grid-cols-4">
          {tiers.map((t) => (
            <div class="flex flex-col rounded-card border p-6" data-reveal>
              <h3 class="font-semibold">{t.display_name}</h3>
              <div class="mt-1 text-2xl font-[430] tracking-headline">
                {formatTierPrice(t.unit_amount, t.currency)}
              </div>
              <div class="mt-2 flex-1 text-sm text-muted-foreground">
                Up to {t.key_quota.toLocaleString()} part–location keys
              </div>
              <a
                href={`${appUrl}/#/signup?plan=${t.tier}`}
                class="mt-4 rounded-full bg-primary px-4 py-2 text-center text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
              >Start free trial</a>
            </div>
          ))}
          <div class="flex flex-col rounded-card border p-6" data-reveal>
            <h3 class="font-semibold">Enterprise</h3>
            <div class="mt-1 text-2xl font-[430] tracking-headline">Contact us</div>
            <div class="mt-2 flex-1 text-sm text-muted-foreground">
              SSO, custom connectors, dedicated support, negotiated terms.
            </div>
            <a
              href="/contact"
              class="mt-4 rounded-full border px-4 py-2 text-center text-sm font-medium transition-colors hover:border-foreground"
            >Book a demo</a>
          </div>
        </div>
      ) : (
        <p class="mt-10 text-sm text-muted-foreground" data-reveal>
          See <a href="/pricing" class="underline transition-colors hover:text-foreground">pricing</a>
          for current self-serve tiers, or <a href="/contact" class="underline transition-colors hover:text-foreground">book a demo</a>.
        </p>
      )}
      <p class="mt-6 text-sm text-muted-foreground" data-reveal>
        Full detail on the <a href="/pricing" class="underline transition-colors hover:text-foreground">pricing page</a>.
      </p>
    </div>
  </section>

  <!-- 9. Closing CTA -->
  <section class="bg-panel text-background">
    <div class="mx-auto max-w-6xl px-6 py-16 text-center sm:py-24" data-reveal>
      <h2 class="mx-auto max-w-2xl text-3xl font-[430] tracking-headline sm:text-5xl">
        Put your inventory under governed autonomy.
      </h2>
      <p class="mx-auto mt-4 max-w-xl text-panel-muted">
        Upload your data, review the first recommendations, and decide exactly how much
        latitude the system gets. 14-day free trial · card required · cancel anytime.
      </p>
      <div class="mt-8 flex flex-wrap justify-center gap-3">
        <a
          href={`${appUrl}/#/signup?plan=growth`}
          class="rounded-full bg-peach px-6 py-3 font-medium text-panel transition-opacity hover:opacity-90"
        >Start free trial</a>
        <a
          href={appUrl}
          class="rounded-full border border-panel-line px-6 py-3 font-medium transition-colors hover:border-background"
        >Sign in ↗</a>
      </div>
    </div>
  </section>
</Base>
```

- [ ] **Step 4: Verify build and full test suite**

```bash
cd apps/site && npm run build && npm test
```

Expected: build clean; all Vitest suites pass (ContactForm + estimator + both islands). Without `PUBLIC_SUPABASE_*` env the homepage teaser renders the fallback paragraph — that is the designed degradation, not a failure.

- [ ] **Step 5: Commit**

```bash
git add apps/site/src/pages/index.astro apps/site/src/pages/pricing.astro apps/site/src/lib/tierFormat.ts
git commit -m "feat(site): rebuild homepage as parent-brand interactive explainer"
```

---

### Task 6: Cross-page verification + in-browser visual QA

> **Note for subagent-driven execution:** this task drives the Browser pane, which only the main session has. Execute this task in the main session (or hand the checklist to an agent that has browser tools).

**Files:**
- Modify: `.claude/launch.json` (add `site-dev` entry)
- Possibly modify: any `apps/site/src/**` file where QA finds a defect (fix inline, keep changes minimal)

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: a verified site + desktop/mobile screenshots shared with the user.

- [ ] **Step 1: Add the dev-server launch config**

In `.claude/launch.json`, add this object to the `configurations` array (after `web-dev`):

```json
{
  "name": "site-dev",
  "runtimeExecutable": "npm",
  "runtimeArgs": ["run", "dev", "--", "--port", "4321", "--strictPort"],
  "cwd": "apps/site",
  "port": 4321,
  "autoPort": false
}
```

- [ ] **Step 2: Start the preview**

Use the browser tool `preview_start` with `{name: "site-dev"}`. Expected: Astro dev server on http://localhost:4321.

- [ ] **Step 3: Homepage functional pass (desktop 1280×800)**

- `read_console_messages` with `onlyErrors: true` → expect zero errors.
- WorkbenchDemo: click **Approve** → "Written to eMRO" badge, ledger entry with `ROP 6→3 · EOQ 12→5 · SS 4→2 · Max 18→8`, counter reaches $9,120. Click **Reset demo** → back to pending, $0.
- SavingsEstimator: drag/`form_input` both sliders → band and per-key line update; assumptions text visible.
- Scroll the full page → each `data-reveal` section fades in once; no layout shift; no horizontal scrollbar.
- Hero headline occupies 2–3 lines at 1280px — **never one word per line, never 4+ lines** (adjust `max-w-*`/text size if it does).
- All CTAs point at the app: hero + nav + pricing cards + closing CTA hrefs contain `aeronta-inventory.vercel.app` (or `PUBLIC_APP_URL`); "Sign in" goes to the app root.

- [ ] **Step 4: Cross-page shell pass**

Visit `/product`, `/pricing`, `/docs`, `/security`, `/contact` in the preview:
- Each renders with the new light shell (no unstyled/black-on-black remnants of the old dark theme).
- `/pricing` shows the Enterprise-only degradation without env — cards render, "Contact us" price, no build/runtime error.
- `/contact` form renders; submit with Supabase unset → the existing error path (`role="alert"`), styled legibly (`text-bad` maps to coral).
- Fix any broken utility class found (a class that silently compiled to nothing under the old preset). Keep fixes to class swaps, not content rewrites.

- [ ] **Step 5: Responsive + accessibility pass**

- `resize_window` to mobile (375×812): nav collapses to the designed subset (Sign in + trial pill + sm-hidden links gone); hero stacks (text above demo panel); demo panel and estimator fully usable; no horizontal scroll.
- `resize_window` back to desktop. Keyboard: Tab reaches nav links, both CTAs, Approve/Reset, both sliders (arrow keys move them), footer links — all with a visible focus indicator.
- Reduced motion: `emulateMedia` isn't exposed — verify via code inspection that `[data-reveal]` has the `prefers-reduced-motion` block (Task 1 CSS) and `WorkbenchDemo` uses `prefersReducedMotion()` before animating; then in the browser confirm the counter still lands on $9,120 (value correctness is motion-independent).
- No-JS: the `<noscript>` style exists in `Base.astro` head; confirm `curl -s http://localhost:4321/ | grep -c "data-reveal"` returns > 0 and the noscript block is present in the HTML (`grep -c "noscript"` ≥ 1).

- [ ] **Step 6: Evidence + fixes**

Take full-page screenshots (desktop and mobile) and send them to the user with `SendUserFile`. If any check in Steps 3–5 failed, fix the source, re-run the failed check, and only then continue.

- [ ] **Step 7: Commit**

```bash
git add .claude/launch.json apps/site
git commit -m "chore(site): visual QA pass — launch config + QA fixes"
```

(If QA found nothing to fix, the commit is just the launch config.)

---

### Task 7: Final gate + bookkeeping

**Files:**
- Modify: `ROADMAP.md` (add/mark the site-redesign line per Section C rules)
- Modify: `TASKS.md` (session log: what's done, what's next)

**Interfaces:**
- Consumes: all prior tasks complete.
- Produces: a merge-ready branch.

- [ ] **Step 1: Full clean-tree verification run**

```bash
cd apps/site && npm run build && npm test && git status --short
```

Expected: build clean, all tests pass, no uncommitted changes except the bookkeeping files you are about to edit.

- [ ] **Step 2: Update ROADMAP.md and TASKS.md**

Add to `ROADMAP.md` (near the other C-track/commercial items) a completed line:

```markdown
- [x] Site explainer redesign — apps/site homepage rebuilt as parent-brand (aeronta.com) interactive explainer (WorkbenchDemo + SavingsEstimator islands, restyled shell) ✅ 2026-07-28
```

Update `TASKS.md` with a dated session entry: shipped the apps/site parent-brand redesign (spec + plan links), and note the one follow-up explicitly deferred by the spec: theme toggle.

- [ ] **Step 3: Commit**

```bash
git add ROADMAP.md TASKS.md
git commit -m "docs: bookkeeping — apps/site explainer redesign shipped"
```

- [ ] **Step 4: Hand back for integration**

Implementation is complete on branch `claude/interactive-creation-ff29aa`. Use the superpowers:finishing-a-development-branch skill to decide merge/PR. Deployment note for the user: the existing separate Vercel project for `apps/site` picks this up on its normal build; a real production build needs `PUBLIC_SUPABASE_URL` / `PUBLIC_SUPABASE_ANON_KEY` / `PUBLIC_APP_URL` / `PUBLIC_SITE_URL` set in that Vercel project (per the C4 rollout runbook) for live pricing tiers.

---

## Self-Review Notes (author)

- **Spec coverage:** brand system → Task 1; hero + demo → Tasks 4–5; problem/how-it-works/BVR/trust/pricing/closing sections → Task 5; estimator → Tasks 2–3; motion + reduced-motion + no-JS → Tasks 1, 4, 6; honesty rules → encoded in component copy and QA checks; scope ripple + compat → Task 6; testing/verification → every task + Task 6–7. Out-of-scope items (theme toggle, apps/web, preset) are not touched by any task.
- **Type consistency:** `SavingsBand`, `estimateSavings`, `perKey`, `formatUsd`, `pct`, `ASSUMPTIONS` names match across Tasks 2/3/4/5; `formatTierPrice` matches across Task 5's two call sites; `Base` props (`contained`) match between Tasks 1 and 5.
- **Numbers cross-checked:** 50M×0.08×0.18=720,000; 50M×0.15×0.25=1,875,000; 10M→144,000/375,000; per-key at 10k keys → $72/$187.5→"$188" via `formatUsd` rounding; demo saving $9,120 consistent between component, tests, and ledger copy.

