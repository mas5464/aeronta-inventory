# Planner UI — Customer-Testing Gap Remediation — Design

**Date:** 2026-07-04
**Status:** Approved (design)
**Sub-project:** #7 Planner UI "Trax IO Review" — a gap-remediation pass surfaced while assessing
readiness for customer testing. Not one of the 4 numbered visual-redesign phases; two of the three
fixes here sit outside the visual-redesign track entirely (a backend correctness bug and a docs
backfill). Phases 1–3 and the ConfidenceHero reference-match refinement are shipped; Phase 4
(navigation shell) is untracked by this spec.

## 1. Context

Two product bugs were flagged-but-deferred during the 2026-07-04 `/frontend-design` review: a raw
internal guardrail code (`non_policy_recommendation`) rendering verbatim as the recommendation
"reason" instead of a human sentence, and the `DemandTrend` chart rendering two flat, maximally-wide
rectangles for parts with sparse demand history. At the time, the user pivoted to the
ConfidenceHero reference-match ask instead of fixing these, so both remained open. A third gap —
`apps/planner-ui/UAT.md`'s "Last validated against" banner and manual-case list frozen at the
184-test Phase-1 baseline while four slices have since shipped (184 → 229 tests: Phase 2, Phase 3,
the ConfidenceHero refinement) with zero corresponding manual cases — surfaced while assessing
customer-testing readiness directly.

All three are independent, individually small fixes bundled into one plan, following this
project's established precedent for bundling small independent fixes (the #8 BVR pipeline's
"6 reviewer-triaged follow-ups" plan did the same).

## 2. Scope

**In scope:**
1. Reason-text humanization (backend correctness bug, `services/agent-spine`)
2. `DemandTrend` chart gap-aware timeline (frontend rendering bug, `apps/planner-ui`)
3. `UAT.md` backfill (documentation)

**Explicitly out of scope:**
- Phase 4 (navigation shell) of the visual redesign — cosmetic, doesn't block correctness-focused
  customer testing.
- The hosting/delivery model for customer testing (local Docker + screen-share vs. a reachable
  URL) — the user's decision to make, not a code gap.
- `total_24mo` trailing-window correctness (§4) — flagged during discovery, needs its own
  investigation into feature-store/extract windowing, not bundled here.

## 3. Reason-text fix

**Root cause, verified against source (not assumed):** `entry.outcome.reasons`
(`services/agent-spine/src/trax_io_spine/guardrail/enforce.py`) is guardrail-pipeline plumbing — a
tuple of short internal codes — that gets joined with `" | "` and used *as* the primary `reason`
text whenever non-empty, at both `QueueRow.reason`
([store.py:327](services/agent-spine/src/trax_io_spine/bff/store.py#L327)) and
`RecommendationDetail.reason`
([store.py:484](services/agent-spine/src/trax_io_spine/bff/store.py#L484)). Since
`entry.outcome.reasons` is non-empty for *every* advisory/non-policy recommendation
([enforce.py:30](services/agent-spine/src/trax_io_spine/guardrail/enforce.py#L30) always prepends
`non_policy_recommendation`) and every hard-rejected one
([enforce.py:42](services/agent-spine/src/trax_io_spine/guardrail/enforce.py#L42)), those rows show
raw codes instead of the recommender's own human-authored `rec.reason` sentence.

**Correction to the originally-presented design:** the code table below replaces one entry from
the version already approved in conversation. `tier_a_requires_approval` — included in that
version — does not exist anywhere in the backend; it turns out to be a placeholder string invented
for the frontend's fake sample data
([apps/planner-ui/src/api/sample.ts:59,92](apps/planner-ui/src/api/sample.ts#L59)), never a real
emitted code. Reading every producer of `rec.guardrail_flags` directly (rather than relying on
memory) surfaced the three real codes it was standing in for the gap on:

| Code | Source | Fires when | Human text |
|---|---|---|---|
| `non_policy_recommendation` | `guardrail/enforce.py:30` | No writable policy change (advisory) | *(dropped — already conveyed by the existing advisory state)* |
| `delta_exceeds_100pct` | `guardrail/hard.py:42` | Spine's re-derived delta check rejects | "Exceeds the 100% single-write cap — requires manual review." |
| `delta_gt_100pct` | `recommenders/adjust_min_max.py:36` | Engine's own pre-check flags the same condition | *(same message as above — collapsed to one, since both commonly co-occur on the same rejected recommendation)* |
| `active_aog` | `risk/aog.py:55` | An aircraft is currently AOG for this part | "An aircraft is currently AOG for this part — routed for immediate review." |
| `shelf_life_clamped` | `policy/constraints.py:41` | Max stock capped to respect shelf life | "Quantity capped to respect this part's shelf life." |
| `hazmat_tool_capped` | `policy/constraints.py:50` | Hazmat/tool-control item; 2× per-cycle cap applied | "Increase capped — hazmat/tool-control items can only double per cycle." |
| `open_order_deferral` | `policy/constraints.py:54` | On-hand + incoming already covers the proposed Max | "Deferred — on-hand stock plus incoming orders already cover the proposed level." |

Any code not in this table (future additions) falls back to a title-cased, underscore-stripped
rendering of the raw string — never silently swallowed, never shown as raw `snake_case` again.

**Fix:**
- New `services/agent-spine/src/trax_io_spine/guardrail/messages.py`:
  `humanize_guardrail_codes(codes: tuple[str, ...]) -> tuple[str, ...]` implementing the table
  above — drops `non_policy_recommendation`, collapses the two delta codes to one message,
  deduplicates, applies the fallback for unknown codes.
- `store.py`: `reason=` becomes unconditionally `rec.reason` at both call sites (`_row()` line 327,
  detail builder line 484) — the guardrail pipeline's codes stop overwriting the recommender's own
  sentence.
- `RecommendationDetail` gains a new field `guardrail_notes: tuple[str, ...]` (Python) /
  `guardrail_notes: string[]` (TS) = `humanize_guardrail_codes(entry.outcome.reasons)`, wired in
  alongside the existing `guardrail_flags=rec.guardrail_flags` line
  ([store.py:496](services/agent-spine/src/trax_io_spine/bff/store.py#L496)). **`guardrail_flags`
  itself is untouched** — grep confirms it is not rendered anywhere in the current UI today (only
  present in type defs, a hook test, and fake sample data), so this is new, additive surface area,
  not a rename or behavior change to an existing field.
- `ConfidenceHero.tsx` gains a new optional prop `guardrailNotes?: string[]`, rendered as a small
  muted note (a short list if more than one) directly after the existing
  `<p className={styles.reason}>{reason}</p>`
  ([ConfidenceHero.tsx:51](apps/planner-ui/src/components/ConfidenceHero.tsx#L51)) and before the
  `evidence.length > 0` Key Findings block — shown only when non-empty.
  `DetailPanel.tsx`'s existing `<ConfidenceHero>` call
  ([DetailPanel.tsx:109](apps/planner-ui/src/components/DetailPanel.tsx#L109)) passes
  `guardrailNotes={detail.guardrail_notes}`.
- `apps/planner-ui/src/api/sample.ts` — replace the two fictional
  `guardrail_flags: ["tier_a_requires_approval"]` sample values (lines 59, 92) with real values,
  and add sample `guardrail_notes` data so the offline/fake UAT path exercises this fix too.

## 4. `DemandTrend` chart fix

Current `apps/planner-ui/src/components/DemandTrend.tsx` (14 lines) positions each bar purely by
array index (`x = i * bw`, `bw = W / points.length`) with no notion of real elapsed time. A part
whose only two demand points are, e.g., 2014 and 2016 (an actual case seen against the live
dataset) renders as two flat, maximally wide, adjacent rectangles spanning nearly the full chart
width — the bug originally flagged.

**Fix:** parse each point's `period_start` into a real `Date`; compute the true span between the
earliest and latest *returned* point (not "today"); position each bar at
`x = padding + (elapsed / totalSpan) * usableWidth`, guarding the degenerate all-points-same-date
case by centering a single fixed-width bar; cap bar width at a fixed maximum instead of deriving it
from point count so sparse data no longer produces oversized bars; add light vertical gridlines at
calendar intervals scaled to the span (yearly for multi-year spans, half-yearly for shorter ones);
add a caption below the chart stating the real observed date range (e.g. "Demand history: Jan 2014
– Jun 2016") instead of implying a fixed "24 months" the data doesn't reliably back. The existing
empty-state (0 points) and single-point behaviors remain explicitly handled, unchanged in spirit.

**Flagged, explicitly out of scope:** the underlying `total_24mo` figure appears to sum the part's
entire observed extract history rather than a true trailing-24-month window, based on 2014–2016
dated points observed against a 2026 "today." That is a feature-store/extract-windowing question —
materially bigger and different than a chart-rendering fix — tracked for separate investigation.

## 5. `UAT.md` backfill

`apps/planner-ui/UAT.md`'s banner ("184 Vitest tests") and manual-case list have not moved since
before Phase 2. Bump the banner to the post-fix test count, then add manual cases (each mapped to
an automated test per the doc's existing `Auto`-column convention) for everything shipped without
one:

- Theme toggle (dark/light switch + persistence across reload) — Phase 1
- Confidence-tier badge in `QueueTable` (color varies by tier) — Phase 2
- `ConfidenceHero` card (bordered card, header row with icon/title/subtitle, decided-status badge
  visibility, gradient percentage by tier, "Why this recommendation?" heading) — Phase 2 +
  reference-match refinement
- Numbered criticality badge in `QueueTable` — Phase 3
- Active-tab count pill — Phase 3
- This plan's two fixes: an advisory/hard-rejected row shows its real recommender explanation (not
  a raw code), plus a humanized guardrail note when applicable; a sparse-data part's `DemandTrend`
  chart shows gap-aware spacing with a real date-range caption

## 6. Testing

- **Backend:** new tests for `humanize_guardrail_codes` covering every real code in the §3 table
  individually, the two-delta-codes-collapse case, and the unknown-code fallback. Extend whichever
  existing `services/agent-spine/tests/bff/` test(s) cover `_row()`/the detail builder to assert
  `reason` is always `rec.reason` and `guardrail_notes` matches the humanized, deduplicated,
  `non_policy_recommendation`-dropped set.
- **Frontend:** `ConfidenceHero.test.tsx` gains cases for the new note (present/absent based on
  `guardrailNotes`). `DemandTrend.test.tsx` (already exists) gains cases for gap-aware spacing on a
  2-point sparse-data fixture, gridline/caption presence, and unchanged empty/single-point
  behavior.
- **Docs:** no automated test for `UAT.md` itself; the plan's final step cross-checks every new
  manual case against a real automated test, per the doc's own convention.
- **Live verification:** per this project's established practice, live-verify all three in a
  browser before considering this complete — confirm a real advisory/rejected row's Drawer shows
  its recommender reason plus a humanized note (not raw codes); confirm a real sparse-data part's
  chart renders gap-aware with gridlines and a caption; confirm nothing regressed in
  `ConfidenceHero`'s existing layout.

## 7. Out of scope, tracked for later

- Phase 4 (navigation shell) of the 4-phase visual redesign.
- The hosting/delivery model decision for customer testing.
- `total_24mo` trailing-window correctness (§4) — needs its own investigation into feature-store/
  extract windowing logic.
