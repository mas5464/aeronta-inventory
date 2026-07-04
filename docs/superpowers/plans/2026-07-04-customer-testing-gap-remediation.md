# Customer-Testing Gap Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the raw guardrail-code "reason" text bug, rewrite the `DemandTrend` chart to space bars by real elapsed time, and backfill `apps/planner-ui/UAT.md`'s manual-case coverage — closing the 3 gaps identified before customer testing.

**Architecture:** A new pure-function humanization layer in `services/agent-spine`'s guardrail package removes raw internal codes from the recommendation `reason` field and exposes them separately, humanized, as `guardrail_notes`; a `ConfidenceHero` prop threads that through to the UI. `DemandTrend` (`apps/planner-ui`) is rewritten to position SVG bars by real elapsed time between demand points instead of array index. `UAT.md` gets two new sections plus two small text corrections.

**Tech Stack:** Python 3.12 / pydantic (`services/agent-spine`, pytest), React 18 + TypeScript + Vite (`apps/planner-ui`, Vitest + Testing Library).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-04-customer-testing-gap-remediation-design.md` (commit `113821c`).
- The guardrail code → message map (spec §3) is verified against source, not assumed — do not add or change codes without re-reading the producers cited in the spec.
- `RecommendationDetail.guardrail_flags` (Python) / `guardrail_flags: string[]` (TS) is **untouched** — it is a different, pre-existing field (the recommender's own flags). The new field is `guardrail_notes`.
- Do not touch Phase 4 (navigation shell) or the `total_24mo` windowing question — both out of scope per spec §2/§7.
- Backend: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest` and `uv run --extra dev ruff check .` must stay green.
- Frontend: `cd apps/planner-ui && npm test && npx tsc -b` must stay green.

---

### Task 1: `humanize_guardrail_codes` — guardrail message humanization

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/guardrail/messages.py`
- Test: `services/agent-spine/tests/guardrail/test_messages.py`

**Interfaces:**
- Produces: `humanize_guardrail_codes(codes: tuple[str, ...]) -> tuple[str, ...]` — consumed by Task 2.

- [ ] **Step 1: Write the failing tests**

Create `services/agent-spine/tests/guardrail/test_messages.py`:

```python
from trax_io_spine.guardrail.messages import humanize_guardrail_codes


def test_drops_non_policy_recommendation() -> None:
    assert humanize_guardrail_codes(("non_policy_recommendation",)) == ()


def test_delta_exceeds_100pct_message() -> None:
    assert humanize_guardrail_codes(("delta_exceeds_100pct",)) == (
        "Exceeds the 100% single-write cap — requires manual review.",
    )


def test_delta_gt_100pct_message_is_identical() -> None:
    assert humanize_guardrail_codes(("delta_gt_100pct",)) == (
        "Exceeds the 100% single-write cap — requires manual review.",
    )


def test_both_delta_codes_collapse_to_one_message() -> None:
    assert humanize_guardrail_codes(("delta_exceeds_100pct", "delta_gt_100pct")) == (
        "Exceeds the 100% single-write cap — requires manual review.",
    )


def test_active_aog_message() -> None:
    assert humanize_guardrail_codes(("active_aog",)) == (
        "An aircraft is currently AOG for this part — routed for immediate review.",
    )


def test_shelf_life_clamped_message() -> None:
    assert humanize_guardrail_codes(("shelf_life_clamped",)) == (
        "Quantity capped to respect this part's shelf life.",
    )


def test_hazmat_tool_capped_message() -> None:
    assert humanize_guardrail_codes(("hazmat_tool_capped",)) == (
        "Increase capped — hazmat/tool-control items can only double per cycle.",
    )


def test_open_order_deferral_message() -> None:
    assert humanize_guardrail_codes(("open_order_deferral",)) == (
        "Deferred — on-hand stock plus incoming orders already cover the proposed level.",
    )


def test_unknown_code_falls_back_to_title_case() -> None:
    assert humanize_guardrail_codes(("some_future_code",)) == ("Some Future Code",)


def test_empty_input_returns_empty() -> None:
    assert humanize_guardrail_codes(()) == ()


def test_dedupes_repeated_codes() -> None:
    assert humanize_guardrail_codes(("active_aog", "active_aog")) == (
        "An aircraft is currently AOG for this part — routed for immediate review.",
    )


def test_preserves_first_seen_order_across_distinct_messages() -> None:
    result = humanize_guardrail_codes(("shelf_life_clamped", "active_aog"))
    assert result == (
        "Quantity capped to respect this part's shelf life.",
        "An aircraft is currently AOG for this part — routed for immediate review.",
    )


def test_full_realistic_rejection_tuple() -> None:
    # A real REJECTED_HARD_GUARDRAIL outcome: the spine's own delta violation +
    # the engine's own pre-check flag + an unrelated engine flag, all in one
    # tuple (mirrors guardrail/enforce.py:42's `violations + rec.guardrail_flags`).
    result = humanize_guardrail_codes(("delta_exceeds_100pct", "delta_gt_100pct", "active_aog"))
    assert result == (
        "Exceeds the 100% single-write cap — requires manual review.",
        "An aircraft is currently AOG for this part — routed for immediate review.",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/guardrail/test_messages.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'trax_io_spine.guardrail.messages'`

- [ ] **Step 3: Write the implementation**

Create `services/agent-spine/src/trax_io_spine/guardrail/messages.py`:

```python
"""Human-readable text for guardrail-pipeline reason codes (GuardrailOutcome.reasons).

These codes are internal plumbing — see guardrail/enforce.py (non_policy_recommendation),
guardrail/hard.py (delta_exceeds_100pct), and the recommendation engine's guardrail_flags
producers (delta_gt_100pct, active_aog, shelf_life_clamped, hazmat_tool_capped,
open_order_deferral) — never meant for display verbatim.
"""

from __future__ import annotations

_DROPPED = frozenset({"non_policy_recommendation"})

_DELTA_CODES = frozenset({"delta_exceeds_100pct", "delta_gt_100pct"})
_DELTA_MESSAGE = "Exceeds the 100% single-write cap — requires manual review."

_MESSAGES: dict[str, str] = {
    "active_aog": "An aircraft is currently AOG for this part — routed for immediate review.",
    "shelf_life_clamped": "Quantity capped to respect this part's shelf life.",
    "hazmat_tool_capped": "Increase capped — hazmat/tool-control items can only double per cycle.",
    "open_order_deferral": (
        "Deferred — on-hand stock plus incoming orders already cover the proposed level."
    ),
}


def _fallback(code: str) -> str:
    return code.replace("_", " ").title()


def humanize_guardrail_codes(codes: tuple[str, ...]) -> tuple[str, ...]:
    """Map raw guardrail reason codes to deduplicated, human-readable messages.

    `non_policy_recommendation` is dropped (already conveyed by the advisory state
    elsewhere in the UI). Both 100%-delta codes collapse to a single message. Any
    code outside the known set falls back to a title-cased rendering of the raw code.
    """
    seen: set[str] = set()
    messages: list[str] = []
    delta_emitted = False
    for code in codes:
        if code in _DROPPED:
            continue
        if code in _DELTA_CODES:
            if not delta_emitted:
                messages.append(_DELTA_MESSAGE)
                delta_emitted = True
            continue
        if code in seen:
            continue
        seen.add(code)
        messages.append(_MESSAGES.get(code, _fallback(code)))
    return tuple(messages)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/guardrail/test_messages.py -v`
Expected: 13 passed

- [ ] **Step 5: Lint**

Run: `cd services/agent-spine && uv run --extra dev ruff check src/trax_io_spine/guardrail/messages.py tests/guardrail/test_messages.py`
Expected: no issues

- [ ] **Step 6: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/guardrail/messages.py services/agent-spine/tests/guardrail/test_messages.py
git commit -m "guardrail: add humanize_guardrail_codes for reason-code display text"
```

---

### Task 2: Wire the reason/guardrail_notes fix into `store.py`

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/models.py:106` (add `guardrail_notes` field)
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py` (new import; `_row()` line 327; `detail()` lines 484, 496)
- Test: `services/agent-spine/tests/bff/test_store_actions.py`

**Interfaces:**
- Consumes: `humanize_guardrail_codes` from Task 1 (`trax_io_spine.guardrail.messages`).
- Produces: `RecommendationDetail.guardrail_notes: tuple[str, ...]` — consumed by Task 3 via the BFF's JSON response (TS mirror: `guardrail_notes: string[]`).

- [ ] **Step 1: Write the failing tests**

Append to `services/agent-spine/tests/bff/test_store_actions.py` (add these three imports to the existing import block at the top, then the three new test functions at the end of the file):

```python
from trax_io_reco.contracts.enums import AutonomyTier
from trax_io_spine.bff.store import _Entry
from trax_io_spine.contracts import GuardrailOutcome, GuardrailStatus
```

```python
def test_reason_is_always_the_recommender_reason(make_rec) -> None:
    store = _store()
    rec = make_rec(
        recommendation_id="r-reason-fix", reason="Recompute levels for steady demand.",
    )
    outcome = GuardrailOutcome(
        recommendation_id="r-reason-fix", status=GuardrailStatus.REJECTED_HARD_GUARDRAIL,
        tier=AutonomyTier.ADVISOR, delta_pct=1.5,
        reasons=("delta_exceeds_100pct", "delta_gt_100pct"),
    )
    store._entries["r-reason-fix"] = _Entry(rec, outcome, TaskStatus.PENDING)

    detail = store.detail("r-reason-fix")
    assert detail.reason == "Recompute levels for steady demand."

    row = store._row(store._entries["r-reason-fix"])
    assert row.reason == "Recompute levels for steady demand."


def test_guardrail_notes_are_humanized_and_deduped(make_rec) -> None:
    store = _store()
    rec = make_rec(recommendation_id="r-notes-fix", reason="test reason")
    outcome = GuardrailOutcome(
        recommendation_id="r-notes-fix", status=GuardrailStatus.REJECTED_HARD_GUARDRAIL,
        tier=AutonomyTier.ADVISOR, delta_pct=1.5,
        reasons=("delta_exceeds_100pct", "delta_gt_100pct", "active_aog"),
    )
    store._entries["r-notes-fix"] = _Entry(rec, outcome, TaskStatus.PENDING)

    detail = store.detail("r-notes-fix")
    assert detail.guardrail_notes == (
        "Exceeds the 100% single-write cap — requires manual review.",
        "An aircraft is currently AOG for this part — routed for immediate review.",
    )


def test_guardrail_notes_empty_for_non_policy_advisory(make_rec) -> None:
    store = _store()
    rec = make_rec(recommendation_id="r-advisory-fix", reason="Advisory reason.", policy=None)
    outcome = GuardrailOutcome(
        recommendation_id="r-advisory-fix", status=GuardrailStatus.QUEUED_FOR_APPROVAL,
        tier=AutonomyTier.ADVISOR, delta_pct=0.0, reasons=("non_policy_recommendation",),
    )
    store._entries["r-advisory-fix"] = _Entry(rec, outcome, TaskStatus.PENDING)

    detail = store.detail("r-advisory-fix")
    assert detail.reason == "Advisory reason."
    assert detail.guardrail_notes == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff/test_store_actions.py -v`
Expected: the 3 new tests FAIL — the first two on the `reason ==` assertions (current code returns the joined raw codes), the third with `AttributeError: 'RecommendationDetail' object has no attribute 'guardrail_notes'`

- [ ] **Step 3: Write the implementation**

In `services/agent-spine/src/trax_io_spine/bff/models.py`, find `class RecommendationDetail` (line 88) and change:

```python
    supporting_evidence: tuple[_EvidenceView, ...]
    guardrail_flags: tuple[str, ...]
    description: str
```

to:

```python
    supporting_evidence: tuple[_EvidenceView, ...]
    guardrail_flags: tuple[str, ...]
    guardrail_notes: tuple[str, ...]
    description: str
```

In `services/agent-spine/src/trax_io_spine/bff/store.py`, add this import to the existing `trax_io_spine`-prefixed import group at the top of the file (alongside `from trax_io_spine.bff.feeds import FEED_DEFINITIONS`):

```python
from trax_io_spine.guardrail.messages import humanize_guardrail_codes
```

Change `_row()`'s reason line from:

```python
            reason=" | ".join(entry.outcome.reasons) or rec.reason,
```

to:

```python
            reason=rec.reason,
```

Change `detail()`'s reason line from:

```python
            status=entry.status, reason=" | ".join(entry.outcome.reasons) or rec.reason,
```

to:

```python
            status=entry.status, reason=rec.reason,
```

In `detail()`, immediately after the existing `guardrail_flags=rec.guardrail_flags,` line, add:

```python
            guardrail_flags=rec.guardrail_flags,
            guardrail_notes=humanize_guardrail_codes(entry.outcome.reasons),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff/ -v`
Expected: all tests pass (the 3 new ones plus every pre-existing test in `tests/bff/` unaffected)

- [ ] **Step 5: Run the full backend suite and lint**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest`
Expected: all green — `RecommendationDetail` gained a required field, so any other test constructing one by hand (not via `store.detail()`) would fail here and need the same `guardrail_notes=()` addition; fix any such failure the same way before proceeding.

Run: `cd services/agent-spine && uv run --extra dev ruff check .`
Expected: no issues

- [ ] **Step 6: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/models.py services/agent-spine/src/trax_io_spine/bff/store.py services/agent-spine/tests/bff/test_store_actions.py
git commit -m "bff: reason is always the recommender's own text; expose humanized guardrail_notes"
```

---

### Task 3: Frontend — `ConfidenceHero` guardrail notes + sample-data cleanup

**Files:**
- Modify: `apps/planner-ui/src/api/types.ts:92` (add `guardrail_notes: string[];`)
- Modify: `apps/planner-ui/src/components/ConfidenceHero.tsx`
- Modify: `apps/planner-ui/src/components/ConfidenceHero.module.css`
- Modify: `apps/planner-ui/src/components/DetailPanel.tsx:109-114`
- Modify: `apps/planner-ui/src/api/sample.ts:59, 92, 125, 156`
- Modify: `apps/planner-ui/src/hooks/usePlanner.test.ts` (fixture helper)
- Test: `apps/planner-ui/src/components/ConfidenceHero.test.tsx`

**Interfaces:**
- Consumes: `RecommendationDetail.guardrail_notes: string[]` (Task 2's field, wire-shape locked by spec §3).
- Produces: `ConfidenceHero`'s new optional prop `guardrailNotes?: string[]`.

- [ ] **Step 1: Write the failing tests**

Append these two `it` blocks inside the existing `describe("ConfidenceHero", ...)` in `apps/planner-ui/src/components/ConfidenceHero.test.tsx` (just before the closing `});` of the describe block):

```tsx
  it("shows guardrail notes when present", () => {
    render(
      <ConfidenceHero
        reason="r"
        confidenceScore={0.5}
        evidence={[]}
        status="pending"
        guardrailNotes={["Exceeds the 100% single-write cap — requires manual review."]}
      />,
    );
    expect(
      screen.getByText("Exceeds the 100% single-write cap — requires manual review."),
    ).toBeInTheDocument();
  });

  it("omits the guardrail notes section when there are none", () => {
    render(
      <ConfidenceHero
        reason="r"
        confidenceScore={0.5}
        evidence={[]}
        status="pending"
        guardrailNotes={[]}
      />,
    );
    expect(screen.queryByText(/requires manual review/)).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npm test -- --run ConfidenceHero`
Expected: the 2 new tests FAIL (TypeScript will also flag `guardrailNotes` as an unknown prop once you check types, but Vitest itself will fail on the missing rendered text first)

- [ ] **Step 3: Write the implementation**

In `apps/planner-ui/src/components/ConfidenceHero.tsx`, change the `Props` interface from:

```tsx
interface Props {
  reason: string;
  confidenceScore: number;
  evidence: EvidenceView[];
  status: TaskStatus;
}
```

to:

```tsx
interface Props {
  reason: string;
  confidenceScore: number;
  evidence: EvidenceView[];
  status: TaskStatus;
  guardrailNotes?: string[];
}
```

Change the function signature from:

```tsx
export function ConfidenceHero({ reason, confidenceScore, evidence, status }: Props) {
```

to:

```tsx
export function ConfidenceHero({ reason, confidenceScore, evidence, status, guardrailNotes = [] }: Props) {
```

Insert this block immediately after `<p className={styles.reason}>{reason}</p>` and before the `{evidence.length > 0 && (` block:

```tsx
      {guardrailNotes.length > 0 && (
        <ul className={styles.guardrailNotes}>
          {guardrailNotes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
```

In `apps/planner-ui/src/components/ConfidenceHero.module.css`, append:

```css
.guardrailNotes {
  margin: -4px 0 10px;
  padding-left: 10px;
  border-left: 2px solid var(--border);
  list-style: none;
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.6;
}
```

In `apps/planner-ui/src/components/DetailPanel.tsx`, change:

```tsx
      <ConfidenceHero
        reason={detail.reason}
        confidenceScore={detail.confidence_score}
        evidence={detail.supporting_evidence}
        status={detail.status}
      />
```

to:

```tsx
      <ConfidenceHero
        reason={detail.reason}
        confidenceScore={detail.confidence_score}
        evidence={detail.supporting_evidence}
        status={detail.status}
        guardrailNotes={detail.guardrail_notes}
      />
```

In `apps/planner-ui/src/api/types.ts`, in the `RecommendationDetail` interface, change:

```ts
  guardrail_flags: string[];
  description: string;
```

to:

```ts
  guardrail_flags: string[];
  guardrail_notes: string[];
  description: string;
```

In `apps/planner-ui/src/api/sample.ts`, replace the fictional flag on the first entry (`rec-hyd-yyz`, which carries `aog_risk_level: 3`) — change:

```ts
      guardrail_flags: ["tier_a_requires_approval"],
```

(the first occurrence, inside the `rec-hyd-yyz` entry) to:

```ts
      guardrail_flags: ["active_aog"],
      guardrail_notes: ["An aircraft is currently AOG for this part — routed for immediate review."],
```

Replace the second occurrence (inside the `rec-hyd-yow` entry) — change:

```ts
      guardrail_flags: ["tier_a_requires_approval"],
```

to:

```ts
      guardrail_flags: [],
      guardrail_notes: [],
```

For the remaining two entries (`rec-filter-yyz` and `rec-valve-yyz`), each currently has `guardrail_flags: [],` — add `guardrail_notes: [],` immediately after each of those two lines.

In `apps/planner-ui/src/hooks/usePlanner.test.ts`, inside the `detailFor(id)` helper, add `guardrail_notes: [],` immediately after the existing `guardrail_flags: [],` line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npm test`
Expected: all tests pass, including the 2 new `ConfidenceHero` cases

Run: `cd apps/planner-ui && npx tsc -b`
Expected: no type errors

- [ ] **Step 5: Commit**

```bash
git add apps/planner-ui/src/api/types.ts apps/planner-ui/src/components/ConfidenceHero.tsx apps/planner-ui/src/components/ConfidenceHero.module.css apps/planner-ui/src/components/ConfidenceHero.test.tsx apps/planner-ui/src/components/DetailPanel.tsx apps/planner-ui/src/api/sample.ts apps/planner-ui/src/hooks/usePlanner.test.ts
git commit -m "planner-ui: ConfidenceHero renders humanized guardrail notes"
```

---

### Task 4: `DemandTrend` gap-aware timeline rewrite

**Files:**
- Modify: `apps/planner-ui/src/components/DemandTrend.tsx`
- Modify: `apps/planner-ui/src/components/DemandTrend.module.css`
- Test: `apps/planner-ui/src/components/DemandTrend.test.tsx`

**Interfaces:**
- Fully independent of Tasks 1-3 (different subsystem — `DemandTrend` takes only `{ points: DemandPoint[] }`, unchanged prop shape).

- [ ] **Step 1: Write the failing tests**

Replace the full contents of `apps/planner-ui/src/components/DemandTrend.test.tsx` with:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DemandTrend } from "./DemandTrend";

describe("DemandTrend", () => {
  it("renders a labelled chart with a bar per point", () => {
    const points = [
      { period_start: "2026-01-01", removals: 2, issues: 0, total: 2 },
      { period_start: "2026-02-01", removals: 0, issues: 1, total: 1 },
    ];
    render(<DemandTrend points={points} />);
    expect(screen.getByRole("img", { name: /demand/i })).toBeInTheDocument();
  });

  it("shows an empty state with no points", () => {
    render(<DemandTrend points={[]} />);
    expect(screen.getByText(/no demand history/i)).toBeInTheDocument();
  });

  it("positions bars by real elapsed time, not array index", () => {
    const points = [
      { period_start: "2024-01-01", removals: 1, issues: 0, total: 1 },
      { period_start: "2024-01-31", removals: 1, issues: 0, total: 1 },
      { period_start: "2026-01-01", removals: 1, issues: 0, total: 1 },
    ];
    const { container } = render(<DemandTrend points={points} />);
    const rects = container.querySelectorAll("rect");
    expect(rects).toHaveLength(3);
    const [x1, x2, x3] = Array.from(rects).map((r) => Number(r.getAttribute("x")));
    // Bar 1 and bar 2 are 30 days apart out of a ~2-year total span: much
    // closer together than bar 2 and bar 3, which are ~2 years apart.
    expect(x2 - x1).toBeLessThan(x3 - x2);
    expect(x2 - x1).toBeLessThan(20);
  });

  it("caps bar width at a fixed size regardless of point count", () => {
    const points = [
      { period_start: "2026-01-01", removals: 1, issues: 0, total: 1 },
      { period_start: "2026-02-01", removals: 1, issues: 0, total: 1 },
    ];
    const { container } = render(<DemandTrend points={points} />);
    const widths = Array.from(container.querySelectorAll("rect")).map((r) =>
      r.getAttribute("width"),
    );
    expect(widths).toEqual(["10", "10"]);
  });

  it("draws gridlines for a multi-year span", () => {
    const points = [
      { period_start: "2024-01-01", removals: 1, issues: 0, total: 1 },
      { period_start: "2026-01-01", removals: 1, issues: 0, total: 1 },
    ];
    const { container } = render(<DemandTrend points={points} />);
    expect(container.querySelectorAll("line").length).toBeGreaterThan(0);
  });

  it("shows the real observed date range in a caption", () => {
    const points = [
      { period_start: "2024-01-15", removals: 1, issues: 0, total: 1 },
      { period_start: "2026-06-15", removals: 1, issues: 0, total: 1 },
    ];
    render(<DemandTrend points={points} />);
    expect(screen.getByText("Demand history: Jan 2024 – Jun 2026")).toBeInTheDocument();
  });

  it("shows a single date with no range for one point", () => {
    render(
      <DemandTrend points={[{ period_start: "2025-03-01", removals: 1, issues: 0, total: 1 }]} />,
    );
    expect(screen.getByText("Demand history: Mar 2025")).toBeInTheDocument();
    expect(screen.getAllByRole("img")).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd apps/planner-ui && npm test -- --run DemandTrend`
Expected: the 2 pre-existing tests pass; the 5 new tests FAIL (no gridlines, no caption text, bars positioned/sized by the old index-based formula)

- [ ] **Step 3: Write the implementation**

Replace the full contents of `apps/planner-ui/src/components/DemandTrend.tsx` with:

```tsx
import type { DemandPoint } from "../api/types";
import styles from "./DemandTrend.module.css";

const W = 320;
const CHART_H = 66;
const PAD_X = 12;
const BAR_W = 10;
const YEAR_MS = 365.25 * 24 * 60 * 60 * 1000;

function monthYear(d: Date): string {
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

export function DemandTrend({ points }: { points: DemandPoint[] }) {
  if (points.length === 0) return <p className={styles.empty}>No demand history for this part.</p>;

  const times = points.map((p) => new Date(p.period_start).getTime());
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
  const span = maxT - minT;
  const usableW = W - PAD_X * 2;
  const max = Math.max(1, ...points.map((p) => p.total));
  const xFor = (t: number) => PAD_X + (span === 0 ? 0.5 : (t - minT) / span) * usableW;

  const stepMs = span >= 2 * YEAR_MS ? YEAR_MS : YEAR_MS / 2;
  const gridlineTimes: number[] = [];
  if (span > 0) {
    for (let t = minT; t < maxT; t += stepMs) gridlineTimes.push(t);
    gridlineTimes.push(maxT);
  }

  return (
    <>
      <svg
        viewBox={`0 0 ${W} ${CHART_H + 24}`}
        width="100%"
        role="img"
        aria-label="Demand history trend"
        className={styles.chart}
      >
        {gridlineTimes.map((t) => (
          <line key={t} x1={xFor(t)} y1={0} x2={xFor(t)} y2={CHART_H} className={styles.gridline} />
        ))}
        {points.map((p, i) => {
          const h = (p.total / max) * (CHART_H - 8);
          return (
            <rect
              key={p.period_start}
              x={xFor(times[i]) - BAR_W / 2}
              y={CHART_H - h}
              width={BAR_W}
              height={h}
              className={styles.bar}
            />
          );
        })}
      </svg>
      <p className={styles.caption}>
        Demand history: {monthYear(new Date(minT))}
        {span > 0 ? ` – ${monthYear(new Date(maxT))}` : ""}
      </p>
    </>
  );
}
```

Replace the full contents of `apps/planner-ui/src/components/DemandTrend.module.css` with:

```css
.bar {
  fill: var(--text-accent);
}

.empty {
  color: var(--text-muted);
  font-size: 12px;
}

.chart {
  display: block;
}

.gridline {
  stroke: var(--border);
  stroke-width: 1;
}

.caption {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-muted);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npm test -- --run DemandTrend`
Expected: 7 passed

Run: `cd apps/planner-ui && npm test && npx tsc -b`
Expected: full suite green, no type errors

- [ ] **Step 5: Commit**

```bash
git add apps/planner-ui/src/components/DemandTrend.tsx apps/planner-ui/src/components/DemandTrend.module.css apps/planner-ui/src/components/DemandTrend.test.tsx
git commit -m "planner-ui: DemandTrend positions bars by real elapsed time, adds gridlines and a date-range caption"
```

---

### Task 5: `UAT.md` backfill

Do this task **last**, after Tasks 1-4 are committed — it needs the real, final test counts.

**Files:**
- Modify: `apps/planner-ui/UAT.md`

**Interfaces:** None — documentation only, no code.

- [ ] **Step 1: Get the real current counts**

Run: `cd apps/planner-ui && npm test -- --run 2>&1 | tail -5`
Expected: **236 passed** (verified baseline 229 as of this plan's authoring + 2 new `ConfidenceHero` cases from Task 3 + 5 new `DemandTrend` cases from Task 4). Use the actual printed number in Steps 2 and 4 below — if it differs from 236 (e.g. a review loop added or removed a test case), trust the real output over this arithmetic.

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest -q 2>&1 | tail -5`
Expected: **266 passed, 4 skipped** (verified baseline 250 passed / 4 skipped as of this plan's authoring + 13 new tests from Task 1 + 3 new tests from Task 2). Use the actual printed number in Step 4 below if it differs from 266.

- [ ] **Step 2: Fix two stale references to already-shipped copy changes**

`apps/planner-ui/UAT.md` predates the Phase 3 criticality-badge change and the ConfidenceHero-refinement heading rename. In the row for case **A3**, change:

```
| A3 | Observe tier badges & criticality dots | Tier badges A/A/B/C; left dot color reflects criticality (1=red … 4=green) | QueueTable ▸ renders one row per recommendation with its tier badge |
```

to:

```
| A3 | Observe tier badges & criticality badge | Tier badges A/A/B/C; Part column shows a numbered circular criticality badge (1=red … 4=green) | QueueTable ▸ renders one row per recommendation with its tier badge; QueueTable ▸ the row selector is a keyboard-operable button exposing criticality as text |
```

In the row for case **C2**, change:

```
| C2 | Read "Why this is queued" | "Tier A — essentiality 1 (flight-safety). Requires planner approval." | DetailPanel ▸ …reason… |
```

to:

```
| C2 | Read "Why this recommendation?" | "Tier A — essentiality 1 (flight-safety). Requires planner approval." | DetailPanel ▸ …reason… |
```

- [ ] **Step 3: Add two new sections**

Immediately before the `## 4. Traceability & coverage summary` heading, insert:

```markdown
### S. Visual redesign — theme, confidence & criticality (Phases 1–3 + ConfidenceHero refinement)

| ID | Steps | Expected | Auto |
|---|---|---|---|
| S1 | Click the sun/moon toggle in the nav rail, then reload the page | Theme switches between light and dark and the choice persists across reload | useTheme ▸ toggleTheme flips the theme, applies the attribute, and persists it |
| S2 | Observe the Conf. column in the queue | Percentage badge colored by tier (high/medium/low get distinct colors, not one flat color) | QueueTable ▸ colors the confidence badge by tier |
| S3 | Select any recommendation and read the top of the drawer | Bordered card; header row with a sparkle icon, "AI Recommendation", "Powered by predictive analytics" | ConfidenceHero ▸ shows the AI Recommendation header with an icon and subtitle |
| S4 | Select a **decided** recommendation (Decided tab) | The hero card's header shows a status badge (Approved/Rejected/Deferred) matching the row's status | ConfidenceHero ▸ shows the status badge for a decided recommendation |
| S5 | Select a **pending** recommendation | The hero card's header shows no status badge | ConfidenceHero ▸ shows no status badge for a pending recommendation |
| S6 | Read the confidence percentage in the hero card | Large percentage number rendered in a two-color gradient (not a flat color); "confidence score" label sits on its own line below the number | ConfidenceHero ▸ colors the percentage by tier |
| S7 | Read the hero card above the reason paragraph | "Why this recommendation?" heading appears directly above the reason text | ConfidenceHero ▸ shows a 'Why this recommendation?' heading above the reason |
| S8 | Observe the active tab (Pending or Decided) | The active tab shows a small pill badge with its row count; the inactive tab shows no count | Tabs ▸ shows the active tab's count as a badge; Tabs ▸ does not show a count on the inactive tab |

### T. Gap-remediation: reason text & demand chart

| ID | Steps | Expected | Auto |
|---|---|---|---|
| T1 | Select `rec-hyd-yyz` (HYD-PUMP-001 · YYZ) | Reason still reads "Tier A — essentiality 1 (flight-safety). Requires planner approval."; a muted note below it reads "An aircraft is currently AOG for this part — routed for immediate review." | ConfidenceHero ▸ shows guardrail notes when present |
| T2 | Select a row with no guardrail notes (e.g. `rec-hyd-yow`) | No muted note appears below the reason paragraph | ConfidenceHero ▸ omits the guardrail notes section when there are none |
| T3 | (Live mode only — the offline fake client always hand-authors realistic reason text, so this only reproduces against a real backend) Select an advisory or hard-guardrail-rejected recommendation | The reason paragraph shows the recommender's own business explanation, never a raw internal code like `non_policy_recommendation` or `delta_exceeds_100pct` | agent-spine ▸ tests/bff/test_store_actions.py::test_reason_is_always_the_recommender_reason |
| T4 | Select HYD-PUMP-001 · YYZ and read the demand-trend chart | Bars are spaced by real elapsed time between demand points (not evenly by index); a caption below the chart states the real observed date range | DemandTrend ▸ positions bars by real elapsed time, not array index; DemandTrend ▸ shows the real observed date range in a caption |
```

- [ ] **Step 4: Update the three stale count references**

Replace the banner on line 8:

```
- **Last validated against:** detail overlay drawer + URL routing + bulk per-result detail + AAA contrast slice (184 Vitest tests green)
```

with (using the real number from Step 1):

```
- **Last validated against:** customer-testing gap remediation — humanized guardrail reasons, gap-aware demand chart (<REAL_COUNT> Vitest tests green)
```

In `## 4. Traceability & coverage summary`, add two rows to the table (after the `R` row) using the real per-section case counts you just wrote in Step 3 (8 for S, 4 for T; T3 is the one manual-only-in-spirit case since it names a Python test, so list it under Automated since it does have a real automated test, matching this table's existing convention of counting a case as Automated whenever an Auto-column entry names a real test):

```
| S Visual redesign (theme, confidence, criticality) | 8 | 8 | — |
| T Gap-remediation (reason text, demand chart) | 4 | 4 | — |
```

Update the sentence below the table from:

```
Everything else is already covered by the 184 Vitest tests; keep this table in sync as cases are
added so "run the Vitest suite" remains a true automated proxy for this UAT.
```

to (using the real number from Step 1):

```
Everything else is already covered by the <REAL_COUNT> Vitest tests; keep this table in sync as
cases are added so "run the Vitest suite" remains a true automated proxy for this UAT.
```

In `## 5. Per-release checklist`, update:

```
1. `npm test` (184 green) · `npm run build` · `tsc -b` clean.
```

to (using the real number from Step 1):

```
1. `npm test` (<REAL_COUNT> green) · `npm run build` · `tsc -b` clean.
```

In the same checklist's item 2, change:

```
2. Backend regression the UI depends on: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest` (250 green, 4 skipped incl. the env-gated WeasyPrint test — BFF + agent-spine, incl. `/parts` + `/dashboard` + the BVR reports surface), plus the repo-wide suite if backend changed.
```

to (using the real number from Step 1 — expected 266):

```
2. Backend regression the UI depends on: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest` (<REAL_COUNT> green, 4 skipped incl. the env-gated WeasyPrint test — BFF + agent-spine, incl. `/parts` + `/dashboard` + the BVR reports surface + humanized guardrail-reason coverage), plus the repo-wide suite if backend changed.
```

- [ ] **Step 5: Commit**

```bash
git add apps/planner-ui/UAT.md
git commit -m "docs: backfill UAT.md for the visual-redesign slices and this gap-remediation pass"
```

---

## Final verification

1. `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest` — all green.
2. `cd services/agent-spine && uv run --extra dev ruff check .` — clean.
3. `cd apps/planner-ui && npm test && npx tsc -b && npm run build` — all green.
4. Live-verify in a browser against the running Docker stack (rebuild first — `docker compose up -d --build bff ui` from the worktree root):
   - Load a real advisory or hard-guardrail-rejected recommendation's detail drawer; confirm the reason is a real sentence, not a raw code, and that any guardrail notes render as a small muted list below it.
   - Load a real sparse-demand part's drawer; confirm the `DemandTrend` chart shows gap-aware bar spacing, light gridlines, and a caption with the real observed date range.
   - Confirm nothing regressed in `ConfidenceHero`'s existing layout (header, card border, gradient percentage, status badge).
5. Update `CLAUDE.md`/`ROADMAP.md`/`TASKS.md` with the new test counts, per this project's established end-of-slice bookkeeping convention.
