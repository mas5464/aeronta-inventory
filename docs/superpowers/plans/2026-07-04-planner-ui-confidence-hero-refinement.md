# ConfidenceHero Reference-Match Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `ConfidenceHero` closer to the reference site's hero card — a bordered card, an icon+title+subtitle header with a decided-status readout, a bigger gradient confidence number, and a "Why this recommendation?" heading — without adding the vendor/price/condition/validity field grid, which needs data Trax doesn't have.

**Architecture:** Three tasks: (1) three new gradient-endpoint color tokens, computed and verified against the real WCAG math, paired with existing tokens as gradient start-points; (2) the card border and the new header row (icon, title, subtitle, status badge) — entirely independent of the gradient tokens; (3) the gradient itself plus the stacked label and new heading, consuming Task 1's tokens.

**Tech Stack:** React 18, TypeScript 5, Vite 5, Vitest 2 + Testing Library, CSS Modules, `lucide-react` icons (already a dependency).

## Global Constraints

- No new npm dependencies.
- Every task ends green on `npm test -- --run` and `npx tsc -b`.
- The field grid (vendor/part/price/quantity/condition/lead-time/validity) is explicitly OUT of scope — do not add it, even partially.
- Two-column "Key Findings" stays out of scope — the Drawer's 420px width already made this cramped when tested in Phase 2; nothing in this plan changes that.
- Every new color must clear its tier's WCAG threshold with real, computed margin: AAA (7:1) for the high and low gradient end-stops (matching the flat colors they extend), AA (4.5:1) for the medium gradient end-stop (matching `--text-secondary`'s existing tier).
- The status badge, when shown, must reuse `QueueTable.module.css`'s exact existing `.status`/`.status_approved`/`.status_rejected`/`.status_deferred` property values — duplicated into `ConfidenceHero.module.css` (the established pattern in this codebase for "same treatment, different file," e.g. `.approve` is already independently duplicated across `DetailPanel.module.css` and `QueueTable.module.css`), not cross-imported.

---

### Task 1: Confidence-gradient end-stop tokens + contrast verification

All hex values below were computed and verified with the same WCAG relative-luminance math `contrast.ts` already implements, against the hero card's own `--surface-1` background (light `#f3f1ea`, dark `#131316`) — the only surface these tokens ever render against, so they don't need the full `AAA_TEXT_TOKENS`/`AA_TEXT_TOKENS` × all-3-surfaces sweep other bare-text tokens get.

Each tier's gradient runs FROM its existing flat color (already shipped, already verified) TO one new "end-stop" token computed for this task:

| Tier | Scheme | From (existing) | To (new) | Ratio (from) | Ratio (to) | Needs |
|---|---|---|---|---|---|---|
| High | Light | `--confidence-high-fg` `#552a86` | `#3d3f96` | 9.01 | 7.91 | ≥7:1 |
| High | Dark | `--confidence-high-fg` `#d0b3f7` | `#b8c4f7` | 10.13 | 10.84 | ≥7:1 |
| Medium | Light | `--text-secondary` `#5f5e5a` | `#565962` | 5.74 | 6.19 | ≥4.5:1 |
| Medium | Dark | `--text-secondary` `#b4b2a9` | `#a8acb4` | 8.73 | 8.15 | ≥4.5:1 |
| Low | Light | `--text-danger` `#932929` | `#7a3015` | 7.18 | 8.22 | ≥7:1 |
| Low | Dark | `--text-danger` `#f4b0b0` | `#f7b8a0` | 10.32 | 10.88 | ≥7:1 |

**Files:**
- Modify: `apps/planner-ui/src/styles/tokens.css`
- Modify: `apps/planner-ui/src/styles/tokens.contrast.test.ts`

**Interfaces:**
- Produces: `--confidence-high-grad-end`, `--confidence-medium-grad-end`, `--confidence-low-grad-end` (new tokens, both schemes) — consumed by Task 3.

- [ ] **Step 1: Write the failing tests**

In `apps/planner-ui/src/styles/tokens.contrast.test.ts`, add two new arrays and their `it.each` blocks. Find the existing `AA_THEMED_PAIRS`/`AA_THEMED_THRESHOLD` declaration (added in the table & badge conventions phase):

```ts
const AA_THEMED_PAIRS: [string, string][] = [
  ["crit-badge-fg", "crit-1"],
  ["crit-badge-fg", "crit-2"],
  ["crit-badge-fg", "crit-3"],
  ["crit-badge-fg", "crit-4"],
  ["crit-badge-fg", "crit-5"],
];
const AA_THEMED_THRESHOLD = 4.5;
```

Immediately after it, add:

```ts

// ConfidenceHero's gradient end-stops (the refinement pass adding a hero-card gradient
// percentage). These only ever render against the hero card's own --surface-1
// background, never the other surfaces, so they're checked directly against that one
// surface rather than the full AAA_TEXT_TOKENS/AA_TEXT_TOKENS x SURFACES sweep.
const HERO_GRADIENT_AAA_PAIRS: [string, string][] = [
  ["confidence-high-grad-end", "surface-1"],
  ["confidence-low-grad-end", "surface-1"],
];
const HERO_GRADIENT_AA_PAIRS: [string, string][] = [
  ["confidence-medium-grad-end", "surface-1"],
];
```

Then find the existing `it.each(AA_THEMED_PAIRS)` block:

```ts
  it.each(AA_THEMED_PAIRS)("%s on %s is >= 4.5:1", (fg, bg) => {
    expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(AA_THEMED_THRESHOLD);
  });
```

Immediately after it, add:

```ts

  it.each(HERO_GRADIENT_AAA_PAIRS)("%s on %s is >= 7:1", (fg, bg) => {
    expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(THEMED_THRESHOLD);
  });

  it.each(HERO_GRADIENT_AA_PAIRS)("%s on %s is >= 4.5:1", (fg, bg) => {
    expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(AA_THEMED_THRESHOLD);
  });
```

(This reuses the already-declared `THEMED_THRESHOLD` (7.0) and `AA_THEMED_THRESHOLD` (4.5) constants — no new threshold constants needed.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: FAIL — `confidence-high-grad-end`, `confidence-medium-grad-end`, `confidence-low-grad-end` don't exist in `tokens.css` yet, so all 6 new cases fail on `undefined` values.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/styles/tokens.css`, add to the light `:root` block, right after the `--confidence-high-fg`/`--confidence-high-bg` pair:

```css
  /* Gradient end-stops for the ConfidenceHero card's percentage (the reference-match
     refinement). Each gradient runs from the existing flat tier color to one of these —
     computed to independently clear that tier's threshold against --surface-1, the only
     surface these ever render against. */
  --confidence-high-grad-end: #3d3f96;
  --confidence-medium-grad-end: #565962;
  --confidence-low-grad-end: #7a3015;
```

So the relevant part of the light block reads:

```css
  /* High-confidence badge/hero color. A hue not used anywhere else in this
     palette (blue is accent/approve, amber/teal/green are Tier A/B/C) so a
     confidence badge is never visually confused with an autonomy-tier badge. */
  --confidence-high-fg: #552a86;
  --confidence-high-bg: #f3ebfc;

  /* Gradient end-stops for the ConfidenceHero card's percentage (the reference-match
     refinement). Each gradient runs from the existing flat tier color to one of these —
     computed to independently clear that tier's threshold against --surface-1, the only
     surface these ever render against. */
  --confidence-high-grad-end: #3d3f96;
  --confidence-medium-grad-end: #565962;
  --confidence-low-grad-end: #7a3015;
```

And add to the `:root[data-theme="dark"]` block, right after its own `--confidence-high-fg`/`--confidence-high-bg` pair:

```css
  /* Gradient end-stops — see the light block's comment. */
  --confidence-high-grad-end: #b8c4f7;
  --confidence-medium-grad-end: #a8acb4;
  --confidence-low-grad-end: #f7b8a0;
```

So the relevant part of the dark block reads:

```css
  /* High-confidence badge/hero color — see the light block's comment. */
  --confidence-high-fg: #d0b3f7;
  --confidence-high-bg: #26173a;

  /* Gradient end-stops — see the light block's comment. */
  --confidence-high-grad-end: #b8c4f7;
  --confidence-medium-grad-end: #a8acb4;
  --confidence-low-grad-end: #f7b8a0;
```

No other value in either block changes.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: PASS — 77 tests green (71 + 6 new: 2 AAA pairs × 2 schemes + 1 AA pair × 2 schemes).

- [ ] **Step 5: Run the full suite**

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — 225 tests green (219 baseline + 6 from this task). `npx tsc -b` clean.

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/styles/tokens.css src/styles/tokens.contrast.test.ts
git commit -m "planner-ui: add ConfidenceHero gradient end-stop tokens, verified against surface-1"
```

---

### Task 2: Card border + header row (icon, title/subtitle, decided-status badge)

**Files:**
- Modify: `apps/planner-ui/src/components/ConfidenceHero.tsx`
- Modify: `apps/planner-ui/src/components/ConfidenceHero.module.css`
- Modify: `apps/planner-ui/src/components/ConfidenceHero.test.tsx`
- Modify: `apps/planner-ui/src/components/DetailPanel.tsx`

**Interfaces:**
- Consumes: `TaskStatus` from `../api/types` (existing type, `"pending" | "approved" | "rejected" | "deferred"`).
- Produces: `ConfidenceHero` gains a required `status: TaskStatus` prop.

- [ ] **Step 1: Write the failing tests**

`ConfidenceHero`'s existing tests don't pass a `status` prop — since this task makes it required, every existing render/rerender call needs `status="pending"` added, and 3 new tests are added. Replace the entire contents of `apps/planner-ui/src/components/ConfidenceHero.test.tsx`:

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
    render(
      <ConfidenceHero
        reason="Tier A — essentiality 1."
        confidenceScore={0.91}
        evidence={EVIDENCE}
        status="pending"
      />,
    );
    expect(screen.getByText("91%")).toBeInTheDocument();
    expect(screen.getByText("Tier A — essentiality 1.")).toBeInTheDocument();
    expect(screen.getByText("Key findings")).toBeInTheDocument();
    expect(screen.getByText(/Order 3 due 2026-05-04/)).toBeInTheDocument();
    expect(screen.getByText("open order")).toBeInTheDocument(); // typeLabel(kind)
  });

  it("omits the findings section entirely when evidence is empty", () => {
    render(
      <ConfidenceHero
        reason="No supporting evidence yet."
        confidenceScore={0.6}
        evidence={[]}
        status="pending"
      />,
    );
    expect(screen.queryByText("Key findings")).not.toBeInTheDocument();
    expect(screen.getByText("No supporting evidence yet.")).toBeInTheDocument();
  });

  it("colors the percentage by tier", () => {
    const { rerender } = render(
      <ConfidenceHero reason="r" confidenceScore={0.9} evidence={[]} status="pending" />,
    );
    expect(screen.getByText("90%").className).toContain("confHigh");

    rerender(<ConfidenceHero reason="r" confidenceScore={0.6} evidence={[]} status="pending" />);
    expect(screen.getByText("60%").className).toContain("confMedium");

    rerender(<ConfidenceHero reason="r" confidenceScore={0.3} evidence={[]} status="pending" />);
    expect(screen.getByText("30%").className).toContain("confLow");
  });

  it("shows the AI Recommendation header with an icon and subtitle", () => {
    render(<ConfidenceHero reason="r" confidenceScore={0.5} evidence={[]} status="pending" />);
    expect(screen.getByText("AI Recommendation")).toBeInTheDocument();
    expect(screen.getByText("Powered by predictive analytics")).toBeInTheDocument();
  });

  it("shows no status badge for a pending recommendation", () => {
    render(<ConfidenceHero reason="r" confidenceScore={0.5} evidence={[]} status="pending" />);
    expect(screen.queryByText("approved")).not.toBeInTheDocument();
    expect(screen.queryByText("rejected")).not.toBeInTheDocument();
    expect(screen.queryByText("deferred")).not.toBeInTheDocument();
  });

  it("shows the status badge for a decided recommendation", () => {
    render(<ConfidenceHero reason="r" confidenceScore={0.5} evidence={[]} status="approved" />);
    expect(screen.getByText("approved").className).toContain("status_approved");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/components/ConfidenceHero.test.tsx`
Expected: FAIL — a TypeScript error first (`status` doesn't exist on the props type yet), then (once you see the type error) the 3 new tests would additionally fail at runtime once the type error is bypassed: no "AI Recommendation" text, no status badge markup exists yet.

- [ ] **Step 3: Write minimal implementation**

Replace the entire contents of `apps/planner-ui/src/components/ConfidenceHero.tsx`:

```tsx
import { Sparkles } from "lucide-react";
import type { EvidenceView, TaskStatus } from "../api/types";
import { confidenceTier, type ConfidenceTier } from "../lib/confidenceTier";
import { typeLabel } from "../lib/format";
import styles from "./ConfidenceHero.module.css";

interface Props {
  reason: string;
  confidenceScore: number;
  evidence: EvidenceView[];
  status: TaskStatus;
}

const CONF_CLASS: Record<ConfidenceTier, string> = {
  high: styles.confHigh,
  medium: styles.confMedium,
  low: styles.confLow,
};

const STATUS_CLASS: Record<Exclude<TaskStatus, "pending">, string> = {
  approved: styles.status_approved,
  rejected: styles.status_rejected,
  deferred: styles.status_deferred,
};

export function ConfidenceHero({ reason, confidenceScore, evidence, status }: Props) {
  const tier = confidenceTier(confidenceScore);
  return (
    <section className={styles.hero}>
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.iconTile}>
            <Sparkles size={16} aria-hidden="true" />
          </span>
          <div>
            <div className={styles.title}>AI Recommendation</div>
            <div className={styles.subtitle}>Powered by predictive analytics</div>
          </div>
        </div>
        {status !== "pending" && (
          <span className={`${styles.status} ${STATUS_CLASS[status]}`}>{status}</span>
        )}
      </div>
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

(This step deliberately leaves `.top`'s inner structure and the reason/heading exactly as they were — the header row and status badge are the only additions in this task. Task 3 restructures `.top` and adds the "Why this recommendation?" heading.)

Replace the entire contents of `apps/planner-ui/src/components/ConfidenceHero.module.css`:

```css
.hero {
  background: var(--surface-1);
  border: 0.5px solid var(--border);
  border-radius: 12px;
  padding: 14px;
  margin-bottom: 14px;
}

.header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.headerLeft {
  display: flex;
  align-items: center;
  gap: 10px;
}

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

.title {
  font-weight: 600;
  font-size: 13px;
}

.subtitle {
  font-size: 11px;
  color: var(--text-muted);
}

.status {
  display: inline-block;
  font-size: 12px;
  text-transform: capitalize;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--surface-1);
  color: var(--text-secondary);
  flex-shrink: 0;
}

.status_approved {
  background: var(--bg-success);
  color: var(--text-success);
}

.status_rejected {
  background: var(--bg-danger);
  color: var(--text-danger);
}

.status_deferred {
  background: var(--bg-accent);
  color: var(--text-accent);
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

(This step adds `.hero`'s new `border`, plus `.header`/`.headerLeft`/`.iconTile`/`.title`/`.subtitle`/`.status`/`.status_approved`/`.status_rejected`/`.status_deferred` — all new. `.top` through `.evKind` are byte-identical to before; Task 3 changes `.top`/`.confHigh`/`.confMedium`/`.confLow` and adds a heading style.)

In `apps/planner-ui/src/components/DetailPanel.tsx`, replace:

```tsx
      <ConfidenceHero
        reason={detail.reason}
        confidenceScore={detail.confidence_score}
        evidence={detail.supporting_evidence}
      />
```

with:

```tsx
      <ConfidenceHero
        reason={detail.reason}
        confidenceScore={detail.confidence_score}
        evidence={detail.supporting_evidence}
        status={detail.status}
      />
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/components/ConfidenceHero.test.tsx`
Expected: PASS — 6 tests green (3 existing, now passing `status="pending"`, + 3 new).

`DetailPanel.test.tsx` needs no changes — it doesn't assert on `ConfidenceHero`'s internals directly, and `detail.status` already exists on its fixtures (`POLICY_DETAIL`/`ADVISORY_DETAIL` from `SAMPLE_SEED`), so passing it through requires no new test setup. Run it as a checkpoint anyway:

Run: `cd apps/planner-ui && npx vitest run src/components/DetailPanel.test.tsx`
Expected: PASS — unchanged count, all green.

- [ ] **Step 5: Run the full suite**

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — 228 tests green (225 after Task 1 + 3 new from this task). `npx tsc -b` clean.

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/components/ConfidenceHero.tsx src/components/ConfidenceHero.module.css src/components/ConfidenceHero.test.tsx src/components/DetailPanel.tsx
git commit -m "planner-ui: ConfidenceHero gets a bordered card, header row, and decided-status badge"
```

---

### Task 3: Gradient percentage, stacked label, "Why this recommendation?" heading

**Files:**
- Modify: `apps/planner-ui/src/components/ConfidenceHero.tsx`
- Modify: `apps/planner-ui/src/components/ConfidenceHero.module.css`
- Modify: `apps/planner-ui/src/components/ConfidenceHero.test.tsx`

**Interfaces:**
- Consumes: `--confidence-high-grad-end`, `--confidence-medium-grad-end`, `--confidence-low-grad-end` (Task 1).

- [ ] **Step 1: Write the failing test**

In `apps/planner-ui/src/components/ConfidenceHero.test.tsx`, add one test to the `describe("ConfidenceHero", ...)` block, right after the existing `"renders the confidence percentage, reason, and findings"` test:

```tsx

  it("shows a 'Why this recommendation?' heading above the reason", () => {
    render(
      <ConfidenceHero reason="Some reason text." confidenceScore={0.5} evidence={[]} status="pending" />,
    );
    expect(screen.getByText("Why this recommendation?")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/planner-ui && npx vitest run src/components/ConfidenceHero.test.tsx`
Expected: FAIL — no "Why this recommendation?" text exists yet.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/components/ConfidenceHero.tsx`, replace:

```tsx
      <div className={styles.top}>
        <span className={`${styles.score} ${CONF_CLASS[tier]}`}>
          {Math.round(confidenceScore * 100)}%
        </span>
        <span className={styles.scoreLabel}>confidence</span>
      </div>
      <p className={styles.reason}>{reason}</p>
```

with:

```tsx
      <div className={styles.top}>
        <span className={`${styles.score} ${CONF_CLASS[tier]}`}>
          {Math.round(confidenceScore * 100)}%
        </span>
        <span className={styles.scoreLabel}>confidence score</span>
      </div>
      <div className={styles.reasonHeading}>Why this recommendation?</div>
      <p className={styles.reason}>{reason}</p>
```

(The label text changes from "confidence" to "confidence score" to match the reference's exact wording, now that it has room to be more descriptive on its own line below the number.)

In `apps/planner-ui/src/components/ConfidenceHero.module.css`, replace:

```css
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
```

with:

```css
.top {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  margin-bottom: 10px;
}

.score {
  font-size: 40px;
  font-weight: 800;
  line-height: 1;
  background-clip: text;
  -webkit-background-clip: text;
  color: transparent;
}

.confHigh {
  background-image: linear-gradient(135deg, var(--confidence-high-fg), var(--confidence-high-grad-end));
}

.confMedium {
  background-image: linear-gradient(135deg, var(--text-secondary), var(--confidence-medium-grad-end));
}

.confLow {
  background-image: linear-gradient(135deg, var(--text-danger), var(--confidence-low-grad-end));
}

.scoreLabel {
  font-size: 12px;
  color: var(--text-muted);
}
```

And add, right after the `.status_deferred` rule (before `.top`):

```css
.reasonHeading {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 4px;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/components/ConfidenceHero.test.tsx`
Expected: PASS — 7 tests green (6 from Task 2 + 1 new). The existing `"colors the percentage by tier"` test still passes unmodified — it asserts on `className`, which still contains `confHigh`/`confMedium`/`confLow` (the gradient lives in each class's CSS, the class names themselves didn't change).

- [ ] **Step 5: Run the full suite**

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — 229 tests green (228 after Task 2 + 1 new from this task). `npx tsc -b` clean.

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/components/ConfidenceHero.tsx src/components/ConfidenceHero.module.css src/components/ConfidenceHero.test.tsx
git commit -m "planner-ui: ConfidenceHero percentage becomes a per-tier gradient with a stacked label"
```

---

## Final verification (after all 3 tasks)

- [ ] Run the full suite: `cd apps/planner-ui && npm test -- --run` — expect 229 tests, all green.
- [ ] `npx tsc -b` — zero errors.
- [ ] **Live-verify in a browser, not just the test suite** — every phase so far has caught at least one real, test-invisible bug this way. Specifically check via `getComputedStyle`:
  - The hero card's border actually renders (not overridden to transparent/none by some other rule) in both themes.
  - The gradient percentage actually shows a visible color transition (not collapsing to a solid color) — check `background-image` resolves to the expected `linear-gradient(...)` and that `color: transparent` + `background-clip: text` are both taking effect (the text should not render as invisible or as solid black/white).
  - The status badge shows the correct variant (approved/rejected/deferred) for a decided row, and shows nothing for a pending row.
  - **`ConfidenceHero.module.css` is being touched with genuinely new rules for the first time this phase** (the card border, `.header`/`.iconTile`/`.status*`, the gradient technique) — this file has NOT been checked for the CSS-specificity trap that broke Phase 1's Approve button (a new single-class rule losing to a pre-existing more-specific selector in the same file). Trace every new rule against this file's own pre-existing selectors before trusting it renders correctly, then confirm live.
- [ ] Update trackers: `ROADMAP.md`'s #7 section (a new bullet for this refinement, clearly labeled as a Phase 2 follow-up, not a new numbered phase), `TASKS.md` (dated completion entry), `CLAUDE.md` if the `apps/planner-ui` test-count bullet needs bumping (229).
- [ ] Note in the tracker update: the field grid and two-column findings remain explicitly deferred; Phase 4 (navigation shell) is unrelated and untouched.
