# Planner UI Table & Badge Conventions (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a row-count badge to the active queue tab, and replace `QueueTable`'s plain criticality dot with a numbered circular badge — both restyle existing data, no new backend endpoints.

**Architecture:** Three tasks: (1) `Tabs` gains an `activeCount?: number` prop, rendered as a small pill only on the currently-selected tab — `App.tsx` already has the exact number this needs (`p.total`), so this is pure UI wiring; (2) a new `--crit-badge-fg` token, computed and verified against the real WCAG math (a genuine finding during planning: the badge's digit color can't reach the same 7:1 AAA every other badge in this app uses, without retuning already-shipped colors — the user chose to accept AA specifically for this badge, so this task adds a parallel AA-tier check alongside the existing AAA-only harness rather than changing it); (3) wires the new token into `QueueTable`'s Part column, replacing the dot + separate screen-reader-only text with one badge carrying its own `aria-label`.

**Tech Stack:** React 18, TypeScript 5, Vite 5, Vitest 2 + Testing Library, CSS Modules.

## Global Constraints

- No new npm dependencies.
- Every task ends green on `npm test -- --run` and `npx tsc -b`.
- The tab count badge shows only on the currently-active tab, reusing `usePlanner`'s existing `total` — no new data fetching.
- **Deliberate exception to this app's usual AAA policy:** the criticality badge's digit color is verified to only 4.5:1 (AA), not 7:1 (AAA) — a decision made explicitly during planning after computing that no foreground color clears AAA against the existing `--crit-1`..`--crit-5` backgrounds in the light scheme without changing those already-shipped colors. This exception is scoped to exactly one new token pair family (`crit-badge-fg` × `crit-1..5`) — every other token in this app keeps its existing AAA/AA tiering unchanged.
- The existing `--crit-1`..`--crit-5` values themselves do not change in this phase, in either scheme.

---

### Task 1: Tab count badge

**Files:**
- Modify: `apps/planner-ui/src/components/Tabs.tsx`
- Modify: `apps/planner-ui/src/components/Tabs.module.css`
- Modify: `apps/planner-ui/src/components/Tabs.test.tsx`
- Modify: `apps/planner-ui/src/App.tsx`

**Interfaces:**
- Consumes: nothing new — `App.tsx`'s existing `p.total` (from `usePlanner`).
- Produces: `Tabs` gains an `activeCount?: number` prop.

- [ ] **Step 1: Write the failing tests**

In `apps/planner-ui/src/components/Tabs.test.tsx`, add two tests to the `describe("Tabs", ...)` block, right after the existing `"exposes a tablist"` test:

```tsx
  it("shows the active tab's count as a badge", () => {
    render(<Tabs tab="pending" onChange={vi.fn()} activeCount={4} />);
    expect(screen.getByRole("tab", { name: /pending/i })).toHaveTextContent("4");
  });

  it("does not show a count on the inactive tab", () => {
    render(<Tabs tab="pending" onChange={vi.fn()} activeCount={4} />);
    expect(screen.getByRole("tab", { name: /decided/i })).not.toHaveTextContent("4");
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/components/Tabs.test.tsx`
Expected: FAIL — `Tabs` doesn't accept an `activeCount` prop yet, and renders no count text at all, so `toHaveTextContent("4")` fails on the first new test.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/components/Tabs.tsx`, replace:

```tsx
interface Props {
  tab: PlannerTab;
  onChange: (tab: PlannerTab) => void;
}
```

with:

```tsx
interface Props {
  tab: PlannerTab;
  onChange: (tab: PlannerTab) => void;
  // Row count for whichever tab is currently active — only that tab shows a badge.
  // Reuses whatever App.tsx already has (usePlanner's `total`); no new fetching.
  activeCount?: number;
}
```

Replace the function signature:

```tsx
export function Tabs({ tab, onChange }: Props) {
```

with:

```tsx
export function Tabs({ tab, onChange, activeCount }: Props) {
```

Replace the tab-rendering loop:

```tsx
      {TABS.map(({ id, label }) => {
        const selected = id === tab;
        return (
          <button
            key={id}
            id={queueTabId(id)}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={QUEUE_PANEL_ID}
            tabIndex={selected ? 0 : -1}
            className={`${styles.tab} ${selected ? styles.active : ""}`}
            onClick={() => onChange(id)}
            onKeyDown={onKeyDown}
          >
            {label}
          </button>
        );
      })}
```

with:

```tsx
      {TABS.map(({ id, label }) => {
        const selected = id === tab;
        return (
          <button
            key={id}
            id={queueTabId(id)}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={QUEUE_PANEL_ID}
            tabIndex={selected ? 0 : -1}
            className={`${styles.tab} ${selected ? styles.active : ""}`}
            onClick={() => onChange(id)}
            onKeyDown={onKeyDown}
          >
            {label}
            {selected && activeCount !== undefined && (
              <span className={styles.count}>{activeCount}</span>
            )}
          </button>
        );
      })}
```

In `apps/planner-ui/src/components/Tabs.module.css`, add after the existing `.active` rule:

```css
.count {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 999px;
  margin-left: 6px;
  background: var(--surface-1);
  color: var(--text-secondary);
}
```

In `apps/planner-ui/src/App.tsx`, replace:

```tsx
        <Tabs tab={p.tab} onChange={(t) => navigate(`/${t}`)} />
```

with:

```tsx
        <Tabs tab={p.tab} onChange={(t) => navigate(`/${t}`)} activeCount={p.total} />
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/components/Tabs.test.tsx`
Expected: PASS — 7 tests green (5 existing + 2 new).

- [ ] **Step 5: Run the full suite**

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — 209 tests green (207 baseline + 2 new from this task's `Tabs.test.tsx` additions; no count change expected from the `App.tsx` edit itself, but **check `App.test.tsx` specifically**: a tab's accessible name is now "Pending 4" rather than bare "Pending" whenever `p.total` is defined. Every existing `App.test.tsx` assertion that names a tab uses a case-insensitive regex (`{ name: /pending/i }` or similar, not an exact string) per the established pattern in this codebase, so a trailing count should not break any existing match — but actually run the suite and confirm this rather than assuming it; if anything does break, it will be an exact-string tab-name assertion that needs updating to a regex, mirroring how Phase 2's Task 5 handled its own unplanned `App.test.tsx` fallout).

Run: `npx tsc -b`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/components/Tabs.tsx src/components/Tabs.module.css src/components/Tabs.test.tsx src/App.tsx
git commit -m "planner-ui: active tab shows its row count as a badge"
```

---

### Task 2: `--crit-badge-fg` token + AA-tier contrast check

All values below were computed and verified with the same WCAG relative-luminance math `contrast.ts` already implements. Pure black (`#000000`) against the current `--crit-1`..`--crit-5` backgrounds:

| Tier | Light bg | Ratio | Dark bg | Ratio |
|---|---|---|---|---|
| 1 | `#e24b4a` | 5.34 | `#f09595` | 9.44 |
| 2 | `#d85a30` | 5.42 | `#f0997b` | 9.55 |
| 3 | `#ba7517` | 5.64 | `#ef9f27` | 9.66 |
| 4 | `#639922` | 6.11 | `#97c459` | 10.36 |
| 5 | `#1d9e75` | 6.20 | `#5dcaa5` | 10.46 |

Every pairing clears 4.5:1 (AA) with real margin (light: 19–38% over the floor; dark comfortably clears 7:1 AAA too, though only AA is required by this plan's Global Constraints exception). Pure black was chosen over a near-black like `--text-primary`'s `#1a1a18` specifically because `#1a1a18` was checked too and its light-scheme crit-1 pairing (4.431:1) falls *below* the 4.5:1 floor — pure black has enough extra margin to clear every pairing; a softer near-black does not.

**Files:**
- Modify: `apps/planner-ui/src/styles/tokens.css`
- Modify: `apps/planner-ui/src/styles/tokens.contrast.test.ts`

**Interfaces:**
- Produces: `--crit-badge-fg` (new token, declared once — see Step 3) — consumed by Task 3.

- [ ] **Step 1: Write the failing test**

In `apps/planner-ui/src/styles/tokens.contrast.test.ts`, add a new AA-tier themed-pairs section. Replace:

```ts
const THEMED_PAIRS: [string, string][] = [
  ["text-accent", "bg-accent"],
  ["text-danger", "bg-danger"],
  ["text-success", "bg-success"],
  ["tier-a-fg", "tier-a-bg"],
  ["tier-b-fg", "tier-b-bg"],
  ["tier-c-fg", "tier-c-bg"],
  ["action-primary-fg", "action-primary-bg"],
  ["confidence-high-fg", "confidence-high-bg"],
];
const THEMED_THRESHOLD = 7.0; // every themed pair's fg token is in AAA_TEXT_TOKENS-equivalent territory
```

with:

```ts
const THEMED_PAIRS: [string, string][] = [
  ["text-accent", "bg-accent"],
  ["text-danger", "bg-danger"],
  ["text-success", "bg-success"],
  ["tier-a-fg", "tier-a-bg"],
  ["tier-b-fg", "tier-b-bg"],
  ["tier-c-fg", "tier-c-bg"],
  ["action-primary-fg", "action-primary-bg"],
  ["confidence-high-fg", "confidence-high-bg"],
];
const THEMED_THRESHOLD = 7.0; // every themed pair's fg token is in AAA_TEXT_TOKENS-equivalent territory

// A deliberate, narrow exception to the AAA-everywhere policy above: a compact
// circular badge digit on a saturated fill is closer to a status indicator than
// primary body text, and no foreground clears 7:1 against the existing
// --crit-1..--crit-5 backgrounds without retuning those already-shipped colors
// (a decision made explicitly, not a shortcut — see the plan's Task 2 for the
// computed numbers). Kept in its own array/threshold so THEMED_PAIRS above stays
// uniformly AAA.
const AA_THEMED_PAIRS: [string, string][] = [
  ["crit-badge-fg", "crit-1"],
  ["crit-badge-fg", "crit-2"],
  ["crit-badge-fg", "crit-3"],
  ["crit-badge-fg", "crit-4"],
  ["crit-badge-fg", "crit-5"],
];
const AA_THEMED_THRESHOLD = 4.5;
```

Then, right after the existing `it.each(THEMED_PAIRS)` block inside the `describe.each([["light", LIGHT], ["dark", DARK]])(...)` block, add a new `it.each` block:

```ts
  it.each(THEMED_PAIRS)("%s on %s is >= 7:1", (fg, bg) => {
    expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(THEMED_THRESHOLD);
  });

  it.each(AA_THEMED_PAIRS)("%s on %s is >= 4.5:1", (fg, bg) => {
    expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(AA_THEMED_THRESHOLD);
  });
```

(Only the new `it.each(AA_THEMED_PAIRS)` block is added — the existing `it.each(THEMED_PAIRS)` block right above it is unchanged, shown here only so the insertion point is unambiguous.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: FAIL — `crit-badge-fg` doesn't exist in `tokens.css` yet, so `tokens["crit-badge-fg"]` is `undefined` and every new `AA_THEMED_PAIRS` case fails.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/styles/tokens.css`, add to the light `:root` block, right after `--font-sans` (the last line before the block's closing `}`):

```css
  /* Criticality-badge digit color. Declared once (not repeated in the dark block)
     because it needs to stay dark in BOTH schemes — unlike most tokens, the
     --crit-N backgrounds don't invert lightness between themes, so a single
     constant value works for both (verified: clears 4.5:1 AA against every
     --crit-N in both schemes — see the plan's Task 2 for the computed numbers).
     This mirrors how --radius is already declared once, above, for the same reason. */
  --crit-badge-fg: #000000;
```

So the full light block's tail reads:

```css
  /* Criticality dot ramp: 1 (red) .. 5 (green). */
  --crit-1: #e24b4a;
  --crit-2: #d85a30;
  --crit-3: #ba7517;
  --crit-4: #639922;
  --crit-5: #1d9e75;

  /* Criticality-badge digit color. Declared once (not repeated in the dark block)
     because it needs to stay dark in BOTH schemes — unlike most tokens, the
     --crit-N backgrounds don't invert lightness between themes, so a single
     constant value works for both (verified: clears 4.5:1 AA against every
     --crit-N in both schemes — see the plan's Task 2 for the computed numbers).
     This mirrors how --radius is already declared once, above, for the same reason. */
  --crit-badge-fg: #000000;

  --font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
```

Do **not** add `--crit-badge-fg` to the `:root[data-theme="dark"]` block — leaving it undeclared there means the dark theme inherits the same `#000000` value from the base `:root` (the same mechanism `--radius` already relies on), which is exactly what the computed numbers above assume.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: PASS — 71 tests green (61 before this task + 10 new: `AA_THEMED_PAIRS` has 5 entries × 2 schemes).

- [ ] **Step 5: Run the full suite**

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — 219 tests green (209 after Task 1 + 10 from this task). `npx tsc -b` clean.

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/styles/tokens.css src/styles/tokens.contrast.test.ts
git commit -m "planner-ui: add crit-badge-fg token, verified AA against all 5 crit tiers"
```

---

### Task 3: QueueTable criticality badge

**Files:**
- Modify: `apps/planner-ui/src/components/QueueTable.tsx`
- Modify: `apps/planner-ui/src/components/QueueTable.module.css`
- Test: `apps/planner-ui/src/components/QueueTable.test.tsx` (existing test, verify it still passes unmodified — see Step 1)

**Interfaces:**
- Consumes: `--crit-badge-fg` (Task 2).

- [ ] **Step 1: Confirm the existing accessible-name test is the regression check (no new test needed)**

`QueueTable.test.tsx`'s existing `"the row selector is a keyboard-operable button exposing criticality as text"` test asserts `expect(selector).toHaveAccessibleName(/criticality 1/i)` on the row-select `<button>`. Today, that name comes from a separate visually-hidden `<span className={styles.srOnly}>Criticality 1. </span>` inside the button. After this task, the badge itself carries `aria-label="Criticality 1"` instead — per the W3C accessible-name-from-content algorithm, a descendant's own `aria-label` is used in place of its content when computing an ancestor's name, so the button's overall computed name should still include "Criticality 1" and this existing test should keep passing with **no changes to the test file**. This step is a checkpoint, not a code change: run it now to record the baseline before touching any component code.

Run: `cd apps/planner-ui && npx vitest run src/components/QueueTable.test.tsx`
Expected: PASS (baseline, before this task's changes).

- [ ] **Step 2: Replace the dot + sr-only span with the numbered badge**

In `apps/planner-ui/src/components/QueueTable.tsx`, replace:

```tsx
                  <span
                    className={styles.dot}
                    style={{ background: `var(--crit-${r.criticality_tier})` }}
                    aria-hidden="true"
                  />
                  <span className={styles.srOnly}>Criticality {r.criticality_tier}. </span>
```

with:

```tsx
                  <span
                    className={styles.critBadge}
                    style={{ background: `var(--crit-${r.criticality_tier})` }}
                    aria-label={`Criticality ${r.criticality_tier}`}
                  >
                    {r.criticality_tier}
                  </span>
```

- [ ] **Step 3: Restyle for the new badge shape**

In `apps/planner-ui/src/components/QueueTable.module.css`, replace:

```css
.dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-right: 8px;
  vertical-align: middle;
}
```

with:

```css
.critBadge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  margin-right: 8px;
  vertical-align: middle;
  font-size: 11px;
  font-weight: 700;
  color: var(--crit-badge-fg);
}
```

`.srOnly` is used nowhere else in this file (confirmed by grep before writing this plan) — its only consumer was the line just deleted in Step 2, so it is now dead code. Delete it too:

```css
.srOnly {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
```

Remove this entire rule from `apps/planner-ui/src/components/QueueTable.module.css`.

- [ ] **Step 4: Run tests to verify nothing broke**

Run: `cd apps/planner-ui && npx vitest run src/components/QueueTable.test.tsx`
Expected: PASS — identical results to Step 1's baseline (the accessible-name test passes for the reason explained in Step 1; every other test in this file asserts click behavior or unrelated columns, untouched by this change).

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — 219 tests green (unchanged from Task 2 — no new tests in this task).

Run: `npx tsc -b`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
cd apps/planner-ui && git add src/components/QueueTable.tsx src/components/QueueTable.module.css
git commit -m "planner-ui: QueueTable criticality dot becomes a numbered circular badge"
```

---

## Final verification (after all 3 tasks)

- [ ] Run the full suite: `cd apps/planner-ui && npm test -- --run` — expect 219 tests, all green.
- [ ] `npx tsc -b` — zero errors.
- [ ] **Live-verify in a browser, not just the test suite** — Phases 1 and 2 each found a real bug this way that no round of static review caught. Specifically check via `getComputedStyle` (not just a screenshot):
  - The active tab's count badge actually renders with `--surface-1`/`--text-secondary` (not a neutral/transparent fallback) — this is exactly the class of CSS-specificity risk that broke Phase 1's Approve button, so trace `Tabs.module.css`'s `.count` rule against every other selector in that file before trusting it renders correctly, then confirm live.
  - Each of the 5 criticality badges (in a queue with rows spanning multiple tiers) renders its digit legibly, in both light and dark themes — confirm `color` resolves to `#000000` and the background resolves to the correct `--crit-N` value per row.
  - Confirm `QueueTable.module.css`'s new `.critBadge` rule doesn't collide with any pre-existing element-qualified selector in that file (e.g. `.table td`, `.actions button`) the same way Phase 1's bug did — trace the actual selectors, don't assume it's fine by analogy.
- [ ] Update trackers: `ROADMAP.md`'s #7 section (new bullet for this phase), `TASKS.md` (dated completion entry), `CLAUDE.md` if the `apps/planner-ui` test-count bullet needs bumping (219).
- [ ] Note in the tracker update: Phase 4 (navigation shell) remains — a separate spec/plan/build cycle, not part of this phase. Also note the deliberate AA-not-AAA exception for the criticality badge, so a future reader doesn't mistake it for an oversight.
