# ConfidenceHero Turbofan Spinner Icon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `ConfidenceHero`'s generic `lucide-react` `Sparkles` icon with the user's custom animated turbofan-spinner SVG.

**Architecture:** A new dependency-free inline-SVG component (`TurbofanSpinnerIcon`), matching the file-organization convention already established by `DemandTrend.tsx` (a plain `.tsx` file, no separate types file), swapped into `ConfidenceHero`'s icon tile in place of `Sparkles`. The tile's flat background fill is removed since the new icon is self-colored.

**Tech Stack:** React 18 + TypeScript, inline SVG (no new npm dependency).

## Global Constraints

- No new npm dependency — this mirrors `DemandTrend`'s existing dependency-free inline-SVG pattern.
- The 7 blade groups must be transcribed verbatim from the source asset (`~/Downloads/mro-turbofan-spinner-violet.svg`) — exact colors and animation stagger timings, not algorithmically regenerated.
- `viewBox="0 0 240 240"` stays unchanged; only the rendered `width`/`height` shrink (20px, per the approved spec), so no internal coordinate or animation-timing math needs to change.
- `ConfidenceHero.module.css`'s `.iconTile` keeps its `width`/`height`/`border-radius`/flex-centering/`flex-shrink` — only `background`/`color` are removed.
- No new automated test — this is a visual/animation-only change; verify live in a browser instead (matches this project's established practice for gradient/animation work).

---

### Task 1: `TurbofanSpinnerIcon` component + wire into `ConfidenceHero`

**Files:**
- Create: `apps/planner-ui/src/components/TurbofanSpinnerIcon.tsx`
- Modify: `apps/planner-ui/src/components/ConfidenceHero.tsx` (imports + icon tile content)
- Modify: `apps/planner-ui/src/components/ConfidenceHero.module.css` (`.iconTile` rule)

**Interfaces:**
- Produces: `TurbofanSpinnerIcon` — a no-props functional component rendering a fixed 20×20 decorative SVG. Only call site is `ConfidenceHero.tsx`.

- [ ] **Step 1: Create the icon component**

Create `apps/planner-ui/src/components/TurbofanSpinnerIcon.tsx`:

```tsx
export function TurbofanSpinnerIcon() {
  return (
    <svg viewBox="0 0 240 240" width="20" height="20" aria-hidden="true">
      <defs>
        <filter id="glow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur in="SourceGraphic" stdDeviation="3.4" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <g filter="url(#glow)">
        <g>
          <g transform="rotate(0.0000 120 120) translate(120 62)">
            <g transform="rotate(22)">
              <ellipse rx="31" ry="16.5" fill="#E96BFF">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="0.0s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
              <ellipse rx="17.05" ry="7.42" cy="-5.28" fill="#ffffff" opacity="0.22">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="0.0s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
            </g>
          </g>
          <g transform="rotate(51.4286 120 120) translate(120 62)">
            <g transform="rotate(22)">
              <ellipse rx="31" ry="16.5" fill="#D45CFB">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="0.49s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
              <ellipse rx="17.05" ry="7.42" cy="-5.28" fill="#ffffff" opacity="0.22">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="0.49s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
            </g>
          </g>
          <g transform="rotate(102.8571 120 120) translate(120 62)">
            <g transform="rotate(22)">
              <ellipse rx="31" ry="16.5" fill="#B257F8">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="0.97s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
              <ellipse rx="17.05" ry="7.42" cy="-5.28" fill="#ffffff" opacity="0.22">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="0.97s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
            </g>
          </g>
          <g transform="rotate(154.2857 120 120) translate(120 62)">
            <g transform="rotate(22)">
              <ellipse rx="31" ry="16.5" fill="#9159F6">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="1.46s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
              <ellipse rx="17.05" ry="7.42" cy="-5.28" fill="#ffffff" opacity="0.22">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="1.46s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
            </g>
          </g>
          <g transform="rotate(205.7143 120 120) translate(120 62)">
            <g transform="rotate(22)">
              <ellipse rx="31" ry="16.5" fill="#7360F4">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="1.94s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
              <ellipse rx="17.05" ry="7.42" cy="-5.28" fill="#ffffff" opacity="0.22">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="1.94s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
            </g>
          </g>
          <g transform="rotate(257.1429 120 120) translate(120 62)">
            <g transform="rotate(22)">
              <ellipse rx="31" ry="16.5" fill="#5A66F1">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="2.43s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
              <ellipse rx="17.05" ry="7.42" cy="-5.28" fill="#ffffff" opacity="0.22">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="2.43s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
            </g>
          </g>
          <g transform="rotate(308.5714 120 120) translate(120 62)">
            <g transform="rotate(22)">
              <ellipse rx="31" ry="16.5" fill="#4070EE">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="2.91s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
              <ellipse rx="17.05" ry="7.42" cy="-5.28" fill="#ffffff" opacity="0.22">
                <animateTransform
                  attributeName="transform"
                  type="scale"
                  values="0.78;1.14;0.78"
                  dur="3.4s"
                  begin="2.91s"
                  repeatCount="indefinite"
                  calcMode="spline"
                  keyTimes="0;0.5;1"
                  keySplines="0.4 0 0.2 1;0.4 0 0.2 1"
                />
              </ellipse>
            </g>
          </g>
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="0 120 120"
            to="360 120 120"
            dur="14s"
            repeatCount="indefinite"
          />
        </g>
      </g>
    </svg>
  );
}
```

- [ ] **Step 2: Wire it into `ConfidenceHero`**

In `apps/planner-ui/src/components/ConfidenceHero.tsx`, change the import line from:

```tsx
import { Sparkles } from "lucide-react";
```

to:

```tsx
import { TurbofanSpinnerIcon } from "./TurbofanSpinnerIcon";
```

Then change:

```tsx
          <span className={styles.iconTile}>
            <Sparkles size={16} aria-hidden="true" />
          </span>
```

to:

```tsx
          <span className={styles.iconTile}>
            <TurbofanSpinnerIcon />
          </span>
```

- [ ] **Step 3: Drop the tile's flat background fill**

In `apps/planner-ui/src/components/ConfidenceHero.module.css`, change:

```css
.iconTile {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  flex-shrink: 0;
  background: var(--bg-accent);
  color: var(--text-accent);
}
```

to:

```css
.iconTile {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  flex-shrink: 0;
}
```

- [ ] **Step 4: Run the existing test suite to confirm nothing broke**

Run: `cd apps/planner-ui && npm test -- --run ConfidenceHero`
Expected: all `ConfidenceHero.test.tsx` cases still pass — "shows the AI Recommendation header with an icon and subtitle" only asserts the title/subtitle text, not the icon element, so it needs no changes and must still pass unmodified.

Run: `cd apps/planner-ui && npm test && npx tsc -b`
Expected: full suite green at the same total test count as before this change (no tests added or removed), `tsc -b` clean.

- [ ] **Step 5: Commit**

```bash
git add apps/planner-ui/src/components/TurbofanSpinnerIcon.tsx apps/planner-ui/src/components/ConfidenceHero.tsx apps/planner-ui/src/components/ConfidenceHero.module.css
git commit -m "planner-ui: ConfidenceHero uses a custom turbofan spinner icon"
```

- [ ] **Step 6: Live-verify in a browser**

Rebuild/reload the running preview and open a recommendation's detail drawer. Confirm:
- The icon renders in the header tile (in place of the old sparkle icon) and is visibly spinning.
- The glow isn't harshly clipped by the tile's rounded corners — a soft bleed past the edges is expected and fine.
- The tile's background is genuinely gone (not coincidentally the same color as the surrounding card).
- No console errors.

---

## Final verification

1. `cd apps/planner-ui && npm test && npx tsc -b && npm run build` — all green, same test count as before.
2. Live-browser check per Task 1 Step 6.
