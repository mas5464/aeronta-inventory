# Planner UI Confidence & Rationale Treatment (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the QueueTable's confidence column and the Drawer's "why this is queued" content the reference site's hero treatment — a tiered badge in the table, and a prominent confidence number + rationale + findings card in the Drawer — without colliding with the existing Tier A/B/C badge colors.

**Architecture:** A shared pure `confidenceTier()` function (Task 1) is the single source of truth for the high/medium/low boundaries, consumed by both a new QueueTable badge (Task 3) and a new `ConfidenceHero` component (Task 4) that `DetailPanel` composes (Task 5) in place of its old two-column "why queued" layout. Task 2 adds the one new color this needs (a violet high-confidence pair, computed and verified against the existing contrast harness) — medium and low reuse existing tokens, so no other new colors are needed.

**Tech Stack:** React 18, TypeScript 5, Vite 5, Vitest 2 + Testing Library, CSS Modules.

## Global Constraints

- No new npm dependencies.
- Every task ends green on `npm test -- --run` and `npx tsc -b`.
- `tokens.contrast.test.ts`'s tiered thresholds stay in force: 7:1 AAA for `text-primary`/`text-accent`/`text-danger`/`text-success`/`tier-*-fg`/`action-primary-fg`/`confidence-high-fg` (new), 4.5:1 AA for `text-secondary`/`text-muted`. Any new or changed token pair must clear its tier.
- Confidence-tier boundaries (used identically everywhere): **high** = score ≥ 0.8, **medium** = 0.5 ≤ score < 0.8, **low** = score < 0.5.
- Confidence-tier colors: **high** gets a new violet token pair (not reused anywhere else in the palette); **medium** reuses `--text-secondary`/`--surface-1` (no new color); **low** reuses `--text-danger`/`--bg-danger` (no new color) — none of the three may reuse `--tier-a-*`, `--tier-b-*`, `--tier-c-*`, or `--action-primary-*`.
- No component's markup/structure changes except `QueueTable.tsx` (badge markup in one cell) and `DetailPanel.tsx` (composes the new `ConfidenceHero`, removes its old two-column section) — this phase is a presentation change over existing data, not a new-data-plumbing project.

---

### Task 1: Shared `confidenceTier()` function

**Files:**
- Create: `apps/planner-ui/src/lib/confidenceTier.ts`
- Create: `apps/planner-ui/src/lib/confidenceTier.test.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces: `export type ConfidenceTier = "high" | "medium" | "low"` and `export function confidenceTier(score: number): ConfidenceTier` from `lib/confidenceTier.ts` — Tasks 3 and 4 both import this.

- [ ] **Step 1: Write the failing tests**

Create `apps/planner-ui/src/lib/confidenceTier.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { confidenceTier } from "./confidenceTier";

describe("confidenceTier", () => {
  it("classifies 0.8 and above as high", () => {
    expect(confidenceTier(0.8)).toBe("high");
    expect(confidenceTier(0.81)).toBe("high");
    expect(confidenceTier(1)).toBe("high");
  });

  it("classifies 0.5 up to (but not including) 0.8 as medium", () => {
    expect(confidenceTier(0.5)).toBe("medium");
    expect(confidenceTier(0.65)).toBe("medium");
    expect(confidenceTier(0.79)).toBe("medium");
  });

  it("classifies below 0.5 as low", () => {
    expect(confidenceTier(0.49)).toBe("low");
    expect(confidenceTier(0.1)).toBe("low");
    expect(confidenceTier(0)).toBe("low");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/lib/confidenceTier.test.ts`
Expected: FAIL — `Cannot find module './confidenceTier'` (the file doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `apps/planner-ui/src/lib/confidenceTier.ts`:

```ts
export type ConfidenceTier = "high" | "medium" | "low";

export function confidenceTier(score: number): ConfidenceTier {
  if (score >= 0.8) return "high";
  if (score >= 0.5) return "medium";
  return "low";
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/lib/confidenceTier.test.ts`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Commit**

```bash
cd apps/planner-ui && git add src/lib/confidenceTier.ts src/lib/confidenceTier.test.ts
git commit -m "planner-ui: add shared confidenceTier() classification function"
```

---

### Task 2: New violet `confidence-high` tokens + contrast test extension

All hex values below were computed and verified with the same WCAG relative-luminance math `contrast.ts` already implements — every pair clears 7:1 AAA with real margin (8.2–10.9:1 depending on pairing), and the hue (violet) doesn't collide with any existing token (blue `text-accent`/`action-primary`, amber `tier-a`, teal `tier-b`, green `tier-c`, red `text-danger`).

**Files:**
- Modify: `apps/planner-ui/src/styles/tokens.css`
- Modify: `apps/planner-ui/src/styles/tokens.contrast.test.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces: `--confidence-high-fg`, `--confidence-high-bg` (new tokens, both schemes) — consumed by Tasks 3 and 4.

- [ ] **Step 1: Write the failing tests**

In `apps/planner-ui/src/styles/tokens.contrast.test.ts`, replace:

```ts
const AAA_TEXT_TOKENS = ["text-primary", "text-accent", "text-danger", "text-success"];
const AA_TEXT_TOKENS = ["text-secondary", "text-muted"];
const THEMED_PAIRS: [string, string][] = [
  ["text-accent", "bg-accent"],
  ["text-danger", "bg-danger"],
  ["text-success", "bg-success"],
  ["tier-a-fg", "tier-a-bg"],
  ["tier-b-fg", "tier-b-bg"],
  ["tier-c-fg", "tier-c-bg"],
  ["action-primary-fg", "action-primary-bg"],
];
```

with:

```ts
const AAA_TEXT_TOKENS = [
  "text-primary",
  "text-accent",
  "text-danger",
  "text-success",
  "confidence-high-fg",
];
const AA_TEXT_TOKENS = ["text-secondary", "text-muted"];
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
```

(`confidence-high-fg` needs BOTH additions, unlike `action-primary-fg`: it renders as a badge fill in the QueueTable — covered by `THEMED_PAIRS` — but ALSO renders bare on the Drawer hero card's `surface-1` background in Task 4, so it needs the full `AAA_TEXT_TOKENS × SURFACES` check too, the same dual treatment `text-accent` already gets.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: FAIL — `confidence-high-fg`/`confidence-high-bg` don't exist in `tokens.css` yet, so the new `it.each` cases fail on `undefined` values.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/styles/tokens.css`, add to the light `:root` block, right after the `--action-primary-bg`/`--action-primary-fg` pair:

```css
  /* High-confidence badge/hero color. A hue not used anywhere else in this
     palette (blue is accent/approve, amber/teal/green are Tier A/B/C) so a
     confidence badge is never visually confused with an autonomy-tier badge. */
  --confidence-high-fg: #552a86;
  --confidence-high-bg: #f3ebfc;
```

So the full light block reads:

```css
:root {
  --surface-0: #faf9f5;
  --surface-1: #f3f1ea;
  --surface-2: #ffffff;
  --border: rgba(0, 0, 0, 0.1);
  --border-strong: rgba(0, 0, 0, 0.2);
  --text-primary: #1a1a18;
  --text-secondary: #5f5e5a;
  --text-muted: #6e6d67;
  --text-accent: #14508a;
  --bg-accent: #e6f1fb;
  --bg-danger: #fcebeb;
  --text-danger: #932929;
  --bg-success: #e1f5ee;
  --text-success: #0c5844;
  --radius: 8px;

  /* Reserved exclusively for the Approve button (row + drawer) — no other element uses this. */
  --action-primary-bg: #094fc2;
  --action-primary-fg: #ffffff;

  /* High-confidence badge/hero color. A hue not used anywhere else in this
     palette (blue is accent/approve, amber/teal/green are Tier A/B/C) so a
     confidence badge is never visually confused with an autonomy-tier badge. */
  --confidence-high-fg: #552a86;
  --confidence-high-bg: #f3ebfc;

  /* Autonomy-tier palette: A = amber (needs review), B = teal, C = green. B moved off
     blue so a Tier-B badge (a filled pill) is never visually confused with the
     action-primary button above. */
  --tier-a-bg: #faeeda;
  --tier-a-fg: #724409;
  --tier-b-bg: #dcf7f2;
  --tier-b-fg: #095851;
  --tier-c-bg: #eaf3de;
  --tier-c-fg: #27500a;

  /* Criticality dot ramp: 1 (red) .. 5 (green). */
  --crit-1: #e24b4a;
  --crit-2: #d85a30;
  --crit-3: #ba7517;
  --crit-4: #639922;
  --crit-5: #1d9e75;

  --font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
```

And add to the `:root[data-theme="dark"]` block, in the same position:

```css
  /* High-confidence badge/hero color — see the light block's comment. */
  --confidence-high-fg: #d0b3f7;
  --confidence-high-bg: #26173a;
```

So the full dark block reads:

```css
:root[data-theme="dark"] {
  --surface-0: #0a0a0c;
  --surface-1: #131316;
  --surface-2: #1c1c20;
  --border: rgba(255, 255, 255, 0.12);
  --border-strong: rgba(255, 255, 255, 0.22);
  --text-primary: #f3f1ea;
  --text-secondary: #b4b2a9;
  --text-muted: #9c9b95;
  --text-accent: #c7def6;
  --bg-accent: #0c447c;
  --bg-danger: #501313;
  --text-danger: #f4b0b0;
  --bg-success: #04342c;
  --text-success: #74d2b2;

  /* Reserved exclusively for the Approve button (row + drawer) — no other element uses this. */
  --action-primary-bg: #0f47b4;
  --action-primary-fg: #ffffff;

  /* High-confidence badge/hero color — see the light block's comment. */
  --confidence-high-fg: #d0b3f7;
  --confidence-high-bg: #26173a;

  /* Autonomy-tier palette: A = amber, B = teal (moved off blue — see the light block's
     comment), C = green. */
  --tier-a-bg: #412402;
  --tier-a-fg: #fac775;
  --tier-b-bg: #042e2e;
  --tier-b-fg: #3ed7c4;
  --tier-c-bg: #173404;
  --tier-c-fg: #a8cd73;
  --crit-1: #f09595;
  --crit-2: #f0997b;
  --crit-3: #ef9f27;
  --crit-4: #97c459;
  --crit-5: #5dcaa5;
}
```

Note the other values in both blocks are unchanged from the current file — only the two new `--confidence-high-*` lines are added, in both schemes.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: PASS — 61 tests green (53 before this task, +6 from adding `confidence-high-fg` to `AAA_TEXT_TOKENS` — 1 token × 3 surfaces × 2 schemes — and +2 from adding the `confidence-high` pair to `THEMED_PAIRS` — 1 pair × 2 schemes).

- [ ] **Step 5: Run the full suite**

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — 201 tests green (190 baseline + 3 from Task 1 + 8 from this task). `npx tsc -b` clean.

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/styles/tokens.css src/styles/tokens.contrast.test.ts
git commit -m "planner-ui: add violet confidence-high token pair, verified AAA in both schemes"
```

---

### Task 3: QueueTable confidence badge

**Files:**
- Modify: `apps/planner-ui/src/components/QueueTable.tsx`
- Modify: `apps/planner-ui/src/components/QueueTable.module.css`
- Modify: `apps/planner-ui/src/components/QueueTable.test.tsx`

**Interfaces:**
- Consumes: `confidenceTier` from `../lib/confidenceTier` (Task 1); `--confidence-high-fg`/`--confidence-high-bg` (Task 2).

- [ ] **Step 1: Write the failing test**

The existing test asserts the bare decimal. In `apps/planner-ui/src/components/QueueTable.test.tsx`, replace:

```tsx
  it("renders the AOG risk badge and confidence", () => {
    render(<QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.getByText("High")).toBeInTheDocument(); // HYD-PUMP-001·YYZ has aog 3
    expect(screen.getByText("Medium")).toBeInTheDocument(); // ·YOW has aog 2
    expect(screen.getByText("0.78")).toBeInTheDocument(); // confidence
  });
```

with:

```tsx
  it("renders the AOG risk badge and confidence as a percentage", () => {
    render(<QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.getByText("High")).toBeInTheDocument(); // HYD-PUMP-001·YYZ has aog 3
    expect(screen.getByText("Medium")).toBeInTheDocument(); // ·YOW has aog 2
    expect(screen.getByText("78%")).toBeInTheDocument(); // confidence, was 0.78
    expect(screen.queryByText("0.78")).not.toBeInTheDocument();
  });

  it("colors the confidence badge by tier", () => {
    const rows = [
      { ...ROWS[0], recommendation_id: "r-high", confidence_score: 0.95 },
      { ...ROWS[0], recommendation_id: "r-medium", confidence_score: 0.65 },
      { ...ROWS[0], recommendation_id: "r-low", confidence_score: 0.2 },
    ];
    render(<QueueTable rows={rows} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.getByText("95%").className).toContain("confHigh");
    expect(screen.getByText("65%").className).toContain("confMedium");
    expect(screen.getByText("20%").className).toContain("confLow");
  });
```

(Both tests are added to the same `describe("QueueTable", ...)` block, right after the existing tests — the `recommendation_id` overrides on the synthetic rows avoid React key collisions across the three constructed rows.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/components/QueueTable.test.tsx`
Expected: FAIL — `screen.getByText("78%")` finds nothing (the cell still renders `"0.78"`), and `className` assertions fail since no `conf*` classes exist yet.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/components/QueueTable.tsx`, add the import and a tier→class lookup. Replace:

```tsx
import { ArrowDown, ArrowUp } from "lucide-react";
import { AOG_LABEL, TIER_LABEL, type AutonomyTier, type QueueRow } from "../api/types";
import type { SortKey, SortSpec } from "../lib/queryView";
import { money, priority, typeLabel } from "../lib/format";
import styles from "./QueueTable.module.css";
```

with:

```tsx
import { ArrowDown, ArrowUp } from "lucide-react";
import { AOG_LABEL, TIER_LABEL, type AutonomyTier, type QueueRow } from "../api/types";
import { confidenceTier, type ConfidenceTier } from "../lib/confidenceTier";
import type { SortKey, SortSpec } from "../lib/queryView";
import { money, priority, typeLabel } from "../lib/format";
import styles from "./QueueTable.module.css";
```

Then, right after the existing `TIER_CLASS` lookup, add:

```tsx
const CONF_CLASS: Record<ConfidenceTier, string> = {
  high: styles.confHigh,
  medium: styles.confMedium,
  low: styles.confLow,
};
```

Then replace the confidence cell:

```tsx
              <td className={styles.num}>{r.confidence_score.toFixed(2)}</td>
```

with:

```tsx
              <td>
                <span className={`${styles.conf} ${CONF_CLASS[confidenceTier(r.confidence_score)]}`}>
                  {Math.round(r.confidence_score * 100)}%
                </span>
              </td>
```

(Dropping `styles.num`, since this cell is now a badge like Tier/AOG, not a right-aligned number — matching how those two badge columns already render their `<td>`s with no alignment class.)

In `apps/planner-ui/src/components/QueueTable.module.css`, add after the existing `.tierC` rule:

```css
.conf {
  display: inline-block;
  font-size: 12px;
  font-weight: 500;
  padding: 2px 10px;
  border-radius: 999px;
}

.confHigh {
  background: var(--confidence-high-bg);
  color: var(--confidence-high-fg);
}

.confMedium {
  background: var(--surface-1);
  color: var(--text-secondary);
}

.confLow {
  background: var(--bg-danger);
  color: var(--text-danger);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/components/QueueTable.test.tsx`
Expected: PASS — 14 tests green (was 13; the "confidence as a percentage" test replaces the old one in place, and "colors the confidence badge by tier" is new).

- [ ] **Step 5: Run the full suite**

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — 202 tests green (201 + 1 net new from this task's QueueTable.test.tsx changes). `npx tsc -b` clean.

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/components/QueueTable.tsx src/components/QueueTable.module.css src/components/QueueTable.test.tsx
git commit -m "planner-ui: QueueTable confidence column becomes a tiered percentage badge"
```

---

### Task 4: New `ConfidenceHero` component

**Files:**
- Create: `apps/planner-ui/src/components/ConfidenceHero.tsx`
- Create: `apps/planner-ui/src/components/ConfidenceHero.module.css`
- Create: `apps/planner-ui/src/components/ConfidenceHero.test.tsx`

**Interfaces:**
- Consumes: `confidenceTier` from `../lib/confidenceTier` (Task 1); `--confidence-high-fg`/`--confidence-high-bg` (Task 2); `typeLabel` from `../lib/format` (existing); `EvidenceView` from `../api/types` (existing).
- Produces: `export function ConfidenceHero(props: { reason: string; confidenceScore: number; evidence: EvidenceView[] }): JSX.Element` from `components/ConfidenceHero.tsx` — Task 5 composes this into `DetailPanel`.

- [ ] **Step 1: Write the failing tests**

Create `apps/planner-ui/src/components/ConfidenceHero.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { EvidenceView } from "../api/types";
import { ConfidenceHero } from "./ConfidenceHero";

const EVIDENCE: EvidenceView[] = [
  { kind: "open_order", ref_id: "ev-1", detail: "Order 3 due 2026-05-04", as_of: null },
  { kind: "demand_history", ref_id: "ev-2", detail: "14 removals / 24mo", as_of: null },
];

describe("ConfidenceHero", () => {
  it("renders the confidence percentage, reason, and findings", () => {
    render(<ConfidenceHero reason="Tier A — essentiality 1." confidenceScore={0.91} evidence={EVIDENCE} />);
    expect(screen.getByText("91%")).toBeInTheDocument();
    expect(screen.getByText("Tier A — essentiality 1.")).toBeInTheDocument();
    expect(screen.getByText("Key findings")).toBeInTheDocument();
    expect(screen.getByText(/Order 3 due 2026-05-04/)).toBeInTheDocument();
    expect(screen.getByText("open order")).toBeInTheDocument(); // typeLabel(kind)
  });

  it("omits the findings section entirely when evidence is empty", () => {
    render(<ConfidenceHero reason="No supporting evidence yet." confidenceScore={0.6} evidence={[]} />);
    expect(screen.queryByText("Key findings")).not.toBeInTheDocument();
    expect(screen.getByText("No supporting evidence yet.")).toBeInTheDocument();
  });

  it("colors the percentage by tier", () => {
    const { rerender } = render(<ConfidenceHero reason="r" confidenceScore={0.9} evidence={[]} />);
    expect(screen.getByText("90%").className).toContain("confHigh");

    rerender(<ConfidenceHero reason="r" confidenceScore={0.6} evidence={[]} />);
    expect(screen.getByText("60%").className).toContain("confMedium");

    rerender(<ConfidenceHero reason="r" confidenceScore={0.3} evidence={[]} />);
    expect(screen.getByText("30%").className).toContain("confLow");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/components/ConfidenceHero.test.tsx`
Expected: FAIL — `Cannot find module './ConfidenceHero'` (the component doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `apps/planner-ui/src/components/ConfidenceHero.tsx`:

```tsx
import type { EvidenceView } from "../api/types";
import { confidenceTier, type ConfidenceTier } from "../lib/confidenceTier";
import { typeLabel } from "../lib/format";
import styles from "./ConfidenceHero.module.css";

interface Props {
  reason: string;
  confidenceScore: number;
  evidence: EvidenceView[];
}

const CONF_CLASS: Record<ConfidenceTier, string> = {
  high: styles.confHigh,
  medium: styles.confMedium,
  low: styles.confLow,
};

export function ConfidenceHero({ reason, confidenceScore, evidence }: Props) {
  const tier = confidenceTier(confidenceScore);
  return (
    <section className={styles.hero}>
      <div className={styles.top}>
        <span className={`${styles.score} ${CONF_CLASS[tier]}`}>
          {Math.round(confidenceScore * 100)}%
        </span>
        <span className={styles.scoreLabel}>confidence</span>
      </div>
      <p className={styles.reason}>{reason}</p>
      {evidence.length > 0 && (
        <>
          <div className={styles.label}>Key findings</div>
          <ul className={styles.evidence}>
            {evidence.map((e) => (
              <li key={e.ref_id}>
                <span className={styles.evKind}>{typeLabel(e.kind)}</span> {e.detail}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
```

Create `apps/planner-ui/src/components/ConfidenceHero.module.css`:

```css
.hero {
  background: var(--surface-1);
  border-radius: 8px;
  padding: 14px;
  margin-bottom: 14px;
}

.top {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
}

.score {
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
}

.confHigh {
  color: var(--confidence-high-fg);
}

.confMedium {
  color: var(--text-secondary);
}

.confLow {
  color: var(--text-danger);
}

.scoreLabel {
  font-size: 12px;
  color: var(--text-muted);
}

.reason {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0 0 10px;
}

.label {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 6px;
}

.evidence {
  margin: 0;
  padding-left: 0;
  list-style: none;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.7;
}

.evKind {
  color: var(--text-muted);
  text-transform: capitalize;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/components/ConfidenceHero.test.tsx`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Run the full suite**

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — 205 tests green (202 + 3 new from `ConfidenceHero.test.tsx`). `npx tsc -b` clean.

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/components/ConfidenceHero.tsx src/components/ConfidenceHero.module.css src/components/ConfidenceHero.test.tsx
git commit -m "planner-ui: add ConfidenceHero component (percentage + reason + findings)"
```

---

### Task 5: Wire `ConfidenceHero` into `DetailPanel`, remove the old two-column layout

**Files:**
- Modify: `apps/planner-ui/src/components/DetailPanel.tsx`
- Modify: `apps/planner-ui/src/components/DetailPanel.module.css`
- Modify: `apps/planner-ui/src/components/DetailPanel.test.tsx`

**Interfaces:**
- Consumes: `ConfidenceHero` from `./ConfidenceHero` (Task 4).

- [ ] **Step 1: Write the failing tests**

In `apps/planner-ui/src/components/DetailPanel.test.tsx`, add two tests to the `describe("DetailPanel", ...)` block, right after the existing `"renders the current→proposed diff, reason, and evidence"` test:

```tsx
  it("shows the confidence hero above the part context, and no longer duplicates it in the header", () => {
    render(
      <DetailPanel detail={POLICY_DETAIL} onApprove={vi.fn()} onReject={vi.fn()} onDefer={vi.fn()} />,
    );
    expect(screen.getByText("78%")).toBeInTheDocument(); // POLICY_DETAIL.confidence_score is 0.78
    expect(screen.getByText("Key findings")).toBeInTheDocument();
    expect(screen.queryByText(/confidence 0\.78/)).not.toBeInTheDocument();
  });

  it("still shows the confidence hero for advisory recommendations with no policy diff", () => {
    render(
      <DetailPanel detail={ADVISORY_DETAIL} onApprove={vi.fn()} onReject={vi.fn()} onDefer={vi.fn()} />,
    );
    expect(screen.getByText(/advisory — no writable policy change/i)).toBeInTheDocument();
    expect(screen.getByText("Key findings")).toBeInTheDocument();
  });
```

(`POLICY_DETAIL` and `ADVISORY_DETAIL` are the file's existing fixtures, already imported at the top of the file — no new imports needed for this step.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/components/DetailPanel.test.tsx`
Expected: FAIL — `screen.getByText("78%")` finds nothing (the header still shows `"confidence 0.78"` as decimal text, and there's no "Key findings" label yet since `ConfidenceHero` isn't wired in).

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/components/DetailPanel.tsx`, add the import:

```tsx
import { useState } from "react";
import { ConfidenceHero } from "./ConfidenceHero";
import { DemandTrend } from "./DemandTrend";
import type {
  HistoryEntry,
  PartContext,
  PolicyView,
  RecommendationDetail,
  RejectReason,
} from "../api/types";
import { demand, typeLabel } from "../lib/format";
import styles from "./DetailPanel.module.css";
```

Then replace the header + everything through the end of the two-column `.cols` div:

```tsx
      <div className={styles.head}>
        <div>
          <span className={styles.pn}>
            {detail.pn} · {detail.location}
          </span>
          <span className={styles.meta}>
            {" "}
            · {typeLabel(detail.type)} · confidence {detail.confidence_score.toFixed(2)}
          </span>
        </div>
        {detail.provenance_id && <span className={styles.prov}>{detail.provenance_id}</span>}
      </div>

      {partContext && (
        <section className={styles.partContext}>
          <div className={styles.partHead}>{partHeadline(partContext)}</div>
          <p className={styles.partStrip}>
            on hand {partContext.stock ? round(partContext.stock.on_hand) : "—"} · serviceable{" "}
            {partContext.stock ? round(partContext.stock.serviceable) : "—"} · in repair{" "}
            {partContext.stock ? round(partContext.stock.in_repair) : "—"} · need{" "}
            {round(detail.shortage_quantity)} · demand {demand(detail.projected_demand)}/
            {detail.horizon_days}d
          </p>
          {partContext.lead_time && (
            <p className={styles.partStrip}>
              Lead time — promised {partContext.lead_time.promised_days ?? "—"}d · realized{" "}
              {partContext.lead_time.realized_mean_days ?? "—"}d (n=
              {partContext.lead_time.n_observations})
            </p>
          )}
          <p className={styles.partStrip}>
            Open orders — {partContext.open_orders.length} ({round(partContext.total_open_qty)} qty)
          </p>
          <DemandTrend points={partContext.demand?.points ?? []} />
        </section>
      )}

      <div className={styles.cols}>
        <section>
          <div className={styles.label}>Current → proposed</div>
          {advisory ? (
            <p className={styles.advisory}>Advisory — no writable policy change.</p>
          ) : (
            <table className={styles.policy}>
              <tbody>
                {POLICY_FIELDS.map(({ key, label }) => (
                  <tr key={key}>
                    <td className={styles.field}>{label}</td>
                    <td className={styles.diff}>
                      {detail.current_policy ? detail.current_policy[key] : "—"}{" "}
                      <span aria-hidden="true">→</span>{" "}
                      <span className={styles.proposed}>{detail.proposed_policy![key]}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section>
          <div className={styles.label}>Why this is queued</div>
          <p className={styles.reason}>{detail.reason}</p>
          {detail.supporting_evidence.length > 0 && (
            <>
              <div className={styles.label}>Evidence</div>
              <ul className={styles.evidence}>
                {detail.supporting_evidence.map((e) => (
                  <li key={e.ref_id}>
                    <span className={styles.evKind}>{typeLabel(e.kind)}</span> {e.detail}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>
```

with:

```tsx
      <div className={styles.head}>
        <div>
          <span className={styles.pn}>
            {detail.pn} · {detail.location}
          </span>
          <span className={styles.meta}> · {typeLabel(detail.type)}</span>
        </div>
        {detail.provenance_id && <span className={styles.prov}>{detail.provenance_id}</span>}
      </div>

      <ConfidenceHero
        reason={detail.reason}
        confidenceScore={detail.confidence_score}
        evidence={detail.supporting_evidence}
      />

      {partContext && (
        <section className={styles.partContext}>
          <div className={styles.partHead}>{partHeadline(partContext)}</div>
          <p className={styles.partStrip}>
            on hand {partContext.stock ? round(partContext.stock.on_hand) : "—"} · serviceable{" "}
            {partContext.stock ? round(partContext.stock.serviceable) : "—"} · in repair{" "}
            {partContext.stock ? round(partContext.stock.in_repair) : "—"} · need{" "}
            {round(detail.shortage_quantity)} · demand {demand(detail.projected_demand)}/
            {detail.horizon_days}d
          </p>
          {partContext.lead_time && (
            <p className={styles.partStrip}>
              Lead time — promised {partContext.lead_time.promised_days ?? "—"}d · realized{" "}
              {partContext.lead_time.realized_mean_days ?? "—"}d (n=
              {partContext.lead_time.n_observations})
            </p>
          )}
          <p className={styles.partStrip}>
            Open orders — {partContext.open_orders.length} ({round(partContext.total_open_qty)} qty)
          </p>
          <DemandTrend points={partContext.demand?.points ?? []} />
        </section>
      )}

      <section>
        <div className={styles.label}>Current → proposed</div>
        {advisory ? (
          <p className={styles.advisory}>Advisory — no writable policy change.</p>
        ) : (
          <table className={styles.policy}>
            <tbody>
              {POLICY_FIELDS.map(({ key, label }) => (
                <tr key={key}>
                  <td className={styles.field}>{label}</td>
                  <td className={styles.diff}>
                    {detail.current_policy ? detail.current_policy[key] : "—"}{" "}
                    <span aria-hidden="true">→</span>{" "}
                    <span className={styles.proposed}>{detail.proposed_policy![key]}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
```

(The policy-diff `<section>` is unindented one level — it's no longer inside `.cols`, but its own inner JSX is otherwise byte-identical. The "Why this is queued" section is deleted entirely; its content now lives in `ConfidenceHero`.)

Everything from `<section className={styles.history}>` onward is unchanged.

In `apps/planner-ui/src/components/DetailPanel.module.css`, delete these four now-unused rules (their content moved into `ConfidenceHero.module.css` in Task 4, or — for `.cols` — no longer has any JSX referencing it):

```css
.cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

@media (max-width: 560px) {
  .cols {
    grid-template-columns: 1fr;
  }
}
```

```css
.advisory,
.reason {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0 0 12px;
}
```

Replace that second block with (keeping `.advisory` alone, since the policy-diff section still uses it — only `.reason` moved out):

```css
.advisory {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0 0 12px;
}
```

And delete:

```css
.evidence {
  margin: 0;
  padding-left: 0;
  list-style: none;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.7;
}

.evKind {
  color: var(--text-muted);
  text-transform: capitalize;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/components/DetailPanel.test.tsx`
Expected: PASS — all tests green (the two new tests, plus the pre-existing `"renders the current→proposed diff, reason, and evidence"` test, which needs no changes — its assertions target text that now renders via `ConfidenceHero`, which Testing Library finds regardless of which component rendered it).

- [ ] **Step 5: Run the full suite**

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — 207 tests green (205 + 2 new from this task's `DetailPanel.test.tsx` additions). `npx tsc -b` clean.

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/components/DetailPanel.tsx src/components/DetailPanel.module.css src/components/DetailPanel.test.tsx
git commit -m "planner-ui: Drawer leads with ConfidenceHero, drops the old two-column layout"
```

---

## Final verification (after all 5 tasks)

- [ ] Run the full suite: `cd apps/planner-ui && npm test -- --run` — expect 207 tests, all green.
- [ ] `npx tsc -b` — zero errors.
- [ ] **Live-verify in a browser, not just the test suite** — Phase 1's final review found a real CSS-specificity bug (the Approve button's whole fill treatment silently not rendering) that was invisible to 5 rounds of static review and only caught by loading the app and inspecting computed styles. For this phase, specifically check via `getComputedStyle` (not just a screenshot) on: (a) a High-confidence QueueTable badge — confirm its background/color actually resolve to the new violet tokens, not a neutral fallback; (b) a Medium and a Low confidence badge — confirm neutral and danger colors respectively; (c) the Drawer's `ConfidenceHero` — confirm it renders above the part-context strip, the percentage is colored correctly, and the old duplicate "confidence 0.XX" text is gone from the header; (d) an advisory recommendation's Drawer — confirm the hero still renders even though the policy-diff section shows "Advisory — no writable policy change."
- [ ] Update trackers: `ROADMAP.md`'s #7 section (new bullet for this phase), `TASKS.md` (dated completion entry), `CLAUDE.md` if the `apps/planner-ui` test-count bullet needs bumping.
- [ ] Note in the tracker update: Phases 3–4 (table/badge conventions, navigation shell) remain — separate spec/plan/build cycles, not part of this phase.
