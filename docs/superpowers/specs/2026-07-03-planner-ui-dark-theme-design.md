# #7 Planner UI — Dark Theme & Accent Discipline (Phase 1 of 4: Airvoyant-inspired redesign) — Design

**Date:** 2026-07-03
**Status:** Approved (design)
**Sub-project:** #7 Planner UI "Trax IO Review" — Phase 1 of a 4-phase visual redesign inspired by an external reference (an aviation-parts-procurement tool). Phases 2–4 (confidence & rationale treatment, table/badge conventions, navigation shell) are separate, later specs.
**Authoritative inputs:** live observation of the reference site's design patterns (no code or assets copied), the current `apps/planner-ui/src/styles/tokens.css` + `tokens.contrast.test.ts` (built earlier this session)

## 1. Context

The user asked to adopt the reference site's UI/UX for the whole Planner UI. Given the breadth, this was decomposed into 4 independently-specable phases, sequenced as: (1) visual theme, (2) confidence & rationale treatment, (3) table/badge conventions, (4) navigation shell — theme first because every later phase's components will be styled through whatever token system this phase establishes. This spec covers Phase 1 only.

The reference site's most transferable theme-level patterns: a dark, near-black surface family; strict restraint around color — one bold accent reserved exclusively for the single highest-stakes primary action per view; a clean, high-density typographic system. Trax IO's Planner UI is currently light-first (dark exists only as an OS-driven `prefers-color-scheme` variant, not toggleable), and its blue accent is currently overloaded across multiple non-CTA uses (Tier-B badges, focus rings, links, misc. "info" styling).

## 2. Scope

**In scope:**
- Convert the dark variant from OS-media-query-driven to a user-toggleable `data-theme` attribute, defaulting to dark.
- A new near-black dark palette (see §4), with light mode retuned for the same accent discipline.
- Restrict the blue accent to Approve buttons only (row + drawer); remap every other current blue usage (chiefly Tier-B) to a new color.
- A toggle control in `NavRail`.
- Extend the existing `tokens.contrast.test.ts` to the new mechanism and re-verify all pairs.

**Deferred / non-goals (later phases or explicitly out of scope):**
- Confidence-score visual treatment, table rank-badges, tab count-badges, navigation-shell restructuring — Phases 2–4, separate specs.
- No component structure changes in this phase — this is tokens + one small toggle control, not a rebuild of any existing component's markup/layout.
- No change to non-color tokens (spacing, radius, typography scale) — out of scope for a phase focused on color discipline.

## 3. Toggle mechanism

Replace `tokens.css`'s `@media (prefers-color-scheme: dark) { :root { ... } }` block with `:root[data-theme="dark"] { ... }` — the override values move as-is (same custom-property names), just under a different selector. This is mechanically transparent to every consumer: every component already reads `var(--token-name)`; none of them care whether the value came from a media query or an attribute selector.

A new `apps/planner-ui/src/lib/theme.ts` (or a `useTheme` hook in `hooks/`) owns:
- Reading the stored preference from `localStorage["trax-io-theme"]` on mount; if absent, defaulting to `"dark"`.
- Applying it via `document.documentElement.dataset.theme = value`.
- A `toggleTheme()` function that flips the value, re-applies the attribute, and persists to `localStorage`.

No `prefers-color-scheme` fallback tier — dark is the deliberate, explicit default for a first-time visitor, not something inferred from OS settings, per the dark-first decision.

## 4. Color values

Every value below is a **starting point** — final hex values are computed and verified during implementation against the same tiered AAA (7:1) / AA (4.5:1) policy `tokens.contrast.test.ts` already enforces (primary/accent/danger/success/tier-badge text at AAA, secondary/muted at AA), the same discipline used to fix the existing palette earlier this session. This spec fixes the *design intent*, not the literal numbers.

**Dark (near-black, replacing today's `prefers-color-scheme` block):**
- Surfaces: `surface-0 ≈ #0a0a0c`, `surface-1 ≈ #131316`, `surface-2 ≈ #1c1c20`.
- `text-primary` stays a near-white; `text-secondary`/`text-muted` retuned for the new surfaces at AA.
- **Approve accent** (new role: filled-button background, not just text/border): a brightened version of the existing brand blue (`≈ #3b82f6` starting point), verified for white-text-on-fill contrast. Used *only* for the Approve button (row + drawer) — no other element gets this color.
- **Tier B** moves off blue to a teal (`≈ #2dd4bf` starting point) — chosen specifically to stay visually distinct from "this button writes to eMRO."
- Tier A (amber), Tier C (green), the criticality ramp, danger, and success keep their current hues, retuned for legibility against the new near-black surfaces.
- Focus rings: proposed to move off the (now Approve-reserved) accent blue onto a neutral high-contrast outline (e.g. white/near-white at reduced opacity), so a focused-but-inert element never visually reads as "click me to approve." Flagging this explicitly since it wasn't asked about directly — a reasonable default given the new accent-scoping rule, but easy to revisit once built.

**Light mode:** same discipline (blue → Approve-only, Tier-B off blue), with light-appropriate values — not a copy of the dark hex values.

## 5. Toggle UI

A sun/moon icon button at the bottom of `NavRail`, below the existing nav items, separate from the (currently disabled) Settings entry — a theme flip is an instant, one-click affordance, not a navigation destination.

## 6. Testing

- Update `tokens.contrast.test.ts`'s block-extraction logic: it currently locates the dark block by searching for the literal text `prefers-color-scheme: dark`; this must change to locate `[data-theme="dark"]` instead. The 48-pair-matrix structure and thresholds are unchanged — only *which block* supplies the "dark" values changes.
- New tests for the toggle: defaults to `"dark"` with nothing in `localStorage`; persists a user choice; correctly sets `document.documentElement.dataset.theme`.
- No existing Vitest or UAT.md case depends on theme, so no regression risk there — this phase adds a new, independent capability without touching any tested behavior.

## 7. Out of scope, tracked for later phases

Phases 2–4 (confidence & rationale treatment in the Drawer, table rank-badges + tab count-badges, NavRail → top-nav shell) are separate design/plan/build cycles, sequenced after this one lands.
