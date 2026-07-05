# Planner UI — ConfidenceHero Turbofan Spinner Icon — Design

**Date:** 2026-07-05
**Status:** Approved (design)
**Sub-project:** #7 Planner UI "Trax IO Review" — a small icon swap in the already-shipped
`ConfidenceHero` header. Not one of the 4 numbered redesign phases, not part of the
customer-testing gap-remediation slice.

## 1. Context

The user supplied a custom animated SVG icon (`mro-turbofan-spinner-violet.svg` — a rotating
turbofan-blade spinner in a violet-to-blue gradient with a soft glow filter) to replace the
generic `lucide-react` `Sparkles` icon currently used in `ConfidenceHero`'s "AI Recommendation"
header tile — the app's only element explicitly labeled "AI."

## 2. Scope

**In scope:**
- A new inline SVG React component wrapping the user's asset, following the existing
  dependency-free inline-SVG precedent already established by `DemandTrend` in this codebase.
- Swap `ConfidenceHero`'s icon tile from `<Sparkles>` to the new component.
- Drop `.iconTile`'s flat `background: var(--bg-accent)` fill — the icon is self-colored (its own
  violet-to-blue gradient), so a background fill would compete with it rather than complement it.
  The tile keeps its size, centering, and `flex-shrink`.
- Preserve the icon's full animation (continuous rotation + per-blade pulse + glow) exactly as
  authored — confirmed with the user, who chose to keep the motion rather than render a static
  frame.

**Explicitly out of scope:**
- Any change to `QueueTable`'s confidence badge, criticality badge, or any other icon in the
  app — this is a single, isolated swap at one call site.
- Resizing or otherwise redesigning the icon tile itself (still 28px, still centered) — only its
  background fill changes.

## 3. New component

`apps/planner-ui/src/components/TurbofanSpinnerIcon.tsx` — a functional component wrapping the
source SVG's markup, preserving the same `viewBox`, the same `<defs>`/glow `<filter>`, and the
same 7 animated blade groups (each an `<ellipse>` pair rotated to its position with its own
`animateTransform` scale-pulse, all sharing one 14s continuous rotation on the outer group).
Accepts no props — this app has exactly one call site. Rendered at a slightly smaller footprint
than the 28px tile (~20px) so the glow filter's blur has room to breathe without being hard-clipped
against the tile's rounded corners.

## 4. `ConfidenceHero` change

- Remove the `Sparkles` import from `lucide-react`.
- Import and render `<TurbofanSpinnerIcon />` in place of `<Sparkles size={16} aria-hidden="true" />`,
  keeping `aria-hidden="true"` on the icon — it stays purely decorative, since the tile's meaning
  is conveyed by the adjacent "AI Recommendation" text, unchanged by this swap.

## 5. CSS change

`ConfidenceHero.module.css`'s `.iconTile` drops `background: var(--bg-accent);` and
`color: var(--text-accent);` (the `color` property is now unused — the new icon doesn't use
`currentColor`, unlike the lucide icon it replaces). Width, height, border-radius, flex-centering,
and `flex-shrink` are unchanged.

## 6. Testing

- `ConfidenceHero.test.tsx`'s existing "shows the AI Recommendation header with an icon and
  subtitle" test asserts the title/subtitle text, not the icon element itself — read the actual
  current test before implementing to confirm no test change is needed.
- No new automated test for the SVG's visual appearance, matching this project's established
  practice of leaving pure-visual verification (gradients, animation) to live-browser checks
  rather than jsdom-level assertions.
- Live-verify in a browser: the icon renders, spins continuously, the glow doesn't look
  clipped or broken, and the tile's background is genuinely gone (not just coincidentally
  transparent-looking).

## 7. Out of scope, tracked for later

None — this is a fully self-contained, single-purpose change.
