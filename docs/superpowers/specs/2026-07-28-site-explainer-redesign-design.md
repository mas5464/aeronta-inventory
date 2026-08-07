# Design: apps/site explainer redesign (parent-brand interactive homepage)

**Date:** 2026-07-28
**Status:** Approved (user, 2026-07-28)
**Scope:** `apps/site` only. `apps/web` (the app) and the shared `packages/tailwind-preset` are untouched.

## Goal

Rebuild the `apps/site` homepage as an interactive, story-driven explainer of the Aeronta Inventory business — what inventory optimization solves, how governed autonomy works, and where the money comes from — restyled to match the parent brand at https://aeronta.com. The site keeps deploying to its own separate Vercel project; all CTAs route to the app (`PUBLIC_APP_URL`, default https://aeronta-inventory.vercel.app) for sign-in / signup.

## Why

- `apps/site` (C4) is code-complete but visually thin: the homepage is 3 bullet cards, styled with the app's dark Airvoyant tokens, not the parent brand.
- The parent site aeronta.com has a distinct, high-quality identity (editorial headlines, warm light ground, coral/peach accents, dark dashboard mockup panels). The inventory product's marketing surface should read as part of that family.
- "Interactive" is the ask: the page should let a visitor *feel* governed autonomy and estimate savings, not just read about them.

## Brand system (extracted live from aeronta.com, 2026-07-28)

| Token | Value | Notes |
|---|---|---|
| Font | Instrument Sans Variable | self-hosted via `@fontsource-variable/instrument-sans`; no CDN |
| Headline style | weight ~430, letter-spacing −0.04em, line-height ~1.05 | large editorial scale (hero ~55px desktop) |
| Ground | white / warm near-white | light-only for v1; **no theme toggle** (deferred) |
| Ink | `hsl(218 23% 12%)` | primary text + dark panels + primary buttons |
| Muted ink | `hsl(218 8% 35%)` | secondary text |
| Signature coral | `hsl(16 100% 33%)` | accent, links, highlights |
| Signature peach | `hsl(24 95% 73%)` | data bars in dark panels |
| Signature cream | `hsl(38 62% 90%)` | soft accent surfaces |
| Signature forest / mint / yellow | `hsl(126 64% 11%)` / `hsl(155 39% 75%)` / `hsl(47 87% 66%)` | sparingly, status/variety |
| Border | `hsl(0 0% 87%)` | hairlines |
| Radius | 0.75rem | cards, panels, buttons |

**Token architecture:** `apps/site` gets its own `src/styles/brand.css` (parent-brand custom properties) and its `tailwind.config.mjs` maps them locally. It **stops importing** `packages/tailwind-preset/tokens.css` and the shared preset — the Airvoyant system remains the app's identity, not the marketing site's. No changes to `packages/tailwind-preset` or `apps/web`.

## Page outline (homepage, `index.astro`)

Each section has exactly one job. Content is drawn from the real product (design doc, product page, CLAUDE.md) — no invented claims.

1. **Nav** — parent grammar: logo/wordmark "Aeronta Inventory", links (Product, Pricing, Docs, Security, Contact), "Sign in" text link + "Start free trial" dark pill. Both app links use `PUBLIC_APP_URL`.
2. **Hero** — editorial headline about stale spares planning (e.g. "Stop planning spares with numbers that went stale years ago."), sub-line naming the loop (recommend → govern → act on ROP/EOQ/safety stock/max), CTAs (*Start free trial*, *Sign in*), trust checks row (governed write-back · full audit trail · savings attributed in dollars). Hero visual: **interactive Workbench demo panel** (dark navy, labeled "synthetic demo") — see Interactive islands.
3. **The problem** — static min/max values decay: intermittent demand, swinging lead times, AOG exposure on one side, capital buried in overstock on the other. Editorial text + small stale-vs-governed visual contrast.
4. **How it works** — parent's numbered grammar: **01 Recommend** (regime-tuned forecasting — statistical / gradient-boosted / empirical-Bayes; provenance on every number) · **02 Govern** (tiered autonomy, hard guardrails never bypassed, approval queue, per-tenant kill switch) · **03 Act** (the only agent with write permission; before/after ledger; rollback window). Each step carries a mini-visual.
5. **Savings estimator** — interactive island: sliders (part-location keys, on-hand inventory value) → illustrative annual savings band. Honest framing: "illustrative model — your real number comes from the Business Value Report."
6. **Proof: the BVR** — savings attribution components (holding cost, ordering cost, stockout risk), governance rates, "every change traced to dollars," methodology honesty (N of M keys valued).
7. **Trust strip** — guardrails never bypassed regardless of tier; tenant isolation; append-only audit ledger; rollback is never a one-way door.
8. **Pricing teaser** — real tiers from the existing Supabase mirror (`getPricingTiers()`, build-time, degrades to Enterprise-only without env) + Enterprise card. Links into `/pricing` and app signup.
9. **Closing CTA** — dark navy full-bleed panel: trial + sign-in CTAs → app.
10. **Footer** — parent-style quiet footer; keeps existing legal/link content.

## Interactive islands (React, Astro `client:` directives)

1. **`WorkbenchDemo`** (hero) — a synthetic approval-queue panel in the parent's dark-mockup style: one pending recommendation (PN × location, current vs recommended ROP/EOQ/SS/Max, projected saving). Visitor clicks **Approve** → row transitions to "written", an audit-ledger entry appears with before/after values, and a projected-savings counter ticks up. A **Reset** affordance restarts it. All data is hardcoded synthetic; panel is labeled "TRAX eMRO · synthetic demo" exactly in the parent's disclosure grammar.
2. **`SavingsEstimator`** — two sliders (number of part-location keys; approximate on-hand value). Output: an illustrative annual savings **band** (e.g. holding-cost reduction range derived from a simple published assumption set, shown as low–high, never a point estimate). The assumptions are visible on the page. Pure client-side math, unit-tested.

## Motion

- Scroll-reveals via IntersectionObserver + CSS transitions (fade/rise, stagger on numbered steps).
- Counter tick-ups inside `WorkbenchDemo` on approve.
- `prefers-reduced-motion`: all reveals render instantly, counters jump to final value.
- No GSAP, no scroll-jacking — matches the parent site's restraint.

## Honesty rules (hard constraints)

- The demo panel and estimator are explicitly labeled synthetic/illustrative.
- No invented customers, logos, testimonials, or point-estimate savings claims.
- Pricing renders only from the live Supabase mirror (existing wiring); no hardcoded dollar amounts.
- Product claims must be traceable to the design doc / product page (governed autonomy, guardrails, BVR attribution, rollback).

## Scope ripple

- Restyling `Base.astro` + tokens re-skins product / pricing / docs / security / contact automatically. Their **content stays as-is** in this project (light copy touch-ups only if a line clashes with the new shell).
- Existing `ContactForm` island and Supabase pricing lib are reused unchanged.

## Error handling

- Pricing fetch failure at build → existing Enterprise-only degradation path stays.
- Islands are progressive: page reads fully with JS disabled (demo panel shows its static initial state; estimator shows the assumption text and a static example band).

## Testing / verification

- Existing Vitest suite stays green; add unit tests for `SavingsEstimator` math and `WorkbenchDemo` state transitions.
- `npm run build` (Astro) clean.
- Visual QA in browser: desktop + mobile widths, reduced-motion, keyboard focus states; full-page screenshots before delivery.

## Out of scope

- Theme toggle (parent has one; deferred).
- Any change to `apps/web`, the BFF, Supabase schema, or `packages/tailwind-preset`.
- New Vercel project plumbing — `apps/site` already deploys to its own project (C4 runbook).
- Content rewrites of the non-home pages.
