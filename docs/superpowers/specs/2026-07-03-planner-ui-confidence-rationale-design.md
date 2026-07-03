# #7 Planner UI — Confidence & Rationale Treatment (Phase 2 of 4: Airvoyant-inspired redesign) — Design

**Date:** 2026-07-03
**Status:** Approved (design)
**Sub-project:** #7 Planner UI "Trax IO Review" — Phase 2 of a 4-phase visual redesign inspired by an external reference (an aviation-parts-procurement tool). Phase 1 (dark theme + accent discipline) shipped 2026-07-03. Phases 3–4 (table/badge conventions, navigation shell) are separate, later specs.
**Authoritative inputs:** live observation of the reference site's confidence/rationale pattern (no code or assets copied), the current `apps/planner-ui/src/components/DetailPanel.tsx` / `QueueTable.tsx`, and the token/contrast conventions established in Phase 1 (`docs/superpowers/specs/2026-07-03-planner-ui-dark-theme-design.md`)

## 1. Context

The reference site's recommendation detail view leads with an "AI Recommendation" hero: a large confidence percentage, a prose "Why this recommendation?" explanation, and a two-column "Key Findings" list. Trax IO's Planner UI already has all of the underlying data for this — `confidence_score` (0–1 float), `reason` (a prose string), and `supporting_evidence` (a list of `{kind, detail}` items) — but renders it as plain text: an inline "confidence 0.78" next to the Drawer header, a plain "Why this is queued" paragraph, and a single unstyled bulleted evidence list. The QueueTable's own "Conf." column is likewise a bare decimal. This phase is a visual/layout treatment on top of existing data, not new data plumbing.

## 2. Scope

**In scope:**
- A new tiered confidence badge in the QueueTable's "Conf." column (percentage display, colored by tier).
- A new hero-card section in the Drawer, replacing the current plain "Why this is queued" + evidence presentation, repositioned to lead the Drawer body.
- A new, distinct confidence-tier color scheme that does not collide with the existing Tier A/B/C autonomy colors.
- A shared `confidenceTier()` function as the single source of truth for tier boundaries, used by both the badge and the hero.

**Deferred / non-goals (later phases or explicitly out of scope):**
- Icons per evidence `kind` — `kind` is an open-ended string in the API (no fixed enum), so a hardcoded icon map isn't well-grounded in the current data model. Revisit if/when the backend introduces a closed enum.
- Table rank-badges (the criticality dot), tab count-badges, navigation-shell restructuring — Phases 3–4, separate specs.
- No change to the policy-diff (current→proposed) table, part-context strip, writeback history, or approve/reject/defer actions — this phase touches only the confidence/rationale presentation.

## 3. QueueTable confidence badge

The "Conf." column keeps its existing `confidence_score` (0–1) backing value but renders as a small colored pill (visually similar construction to the existing Tier badges — background + foreground token pair, rounded, small caps), showing the value as a percentage (`91%`, not `0.91`) instead of today's bare decimal.

Three tiers, shared with the Drawer hero (§5):
- **High** — confidence ≥ 0.80
- **Medium** — confidence 0.50–0.79
- **Low** — confidence < 0.50

## 4. Confidence-tier color scheme

The QueueTable already uses amber (Tier A), teal (Tier B), and green (Tier C) for autonomy tiers in an adjacent column. Reusing those same hues for confidence tiers — even with an unrelated meaning — makes a row read ambiguously at a glance (e.g. an amber Tier-A badge next to an amber Medium-confidence badge). Rather than invent three more saturated colors, only the tier *extremes* get a color signal:

- **High:** a new violet/purple token pair (`--confidence-high-fg` / `--confidence-high-bg`, or equivalent naming) — a hue not used anywhere else in the current palette. Exact hex values are a starting point, computed and verified against the existing tiered AAA(7:1)/AA(4.5:1) contrast policy during implementation, in both light and dark schemes — same discipline `tokens.contrast.test.ts` already enforces.
- **Medium:** no new saturated color — a neutral badge using existing muted tokens (`--text-secondary` on `--surface-1` or `--surface-2`). The "normal" middle case shouldn't visually compete with the two extremes that actually warrant attention.
- **Low:** reuses the existing `--text-danger` / `--bg-danger` pair. "Low confidence" and "needs scrutiny" are semantically compatible with the app's existing danger convention, and reusing it avoids a third new color while still being visually distinct from every Tier badge (none of which use danger red).

Net: one new token pair, zero collisions with Tier A/B/C, and the confidence badge in the QueueTable and the hero's big number (§5) both derive their color from the same `confidenceTier()` function (§6) — never two separate color decisions that could drift apart.

## 5. Drawer hero card

A new section is inserted into the Drawer, immediately after the header and before the existing part-context strip. The Drawer's section order becomes:

**Header → Hero → part-context strip (on-hand/serviceable/demand + trend chart) → policy diff (current → proposed) → writeback history → actions**

The hero card itself (rendered in a `surface-1` panel, single column — the Drawer's 420px width doesn't comfortably fit the reference site's two-column findings layout, which was verified visually before choosing this) contains, top to bottom:
1. The confidence percentage, large and bold, colored per §4.
2. The `reason` prose (unchanged content, new visual treatment — larger, more breathing room than today's plain paragraph).
3. A "Key findings" label, then the existing `supporting_evidence` list — single column, each item showing its `kind` label (text, unchanged formatting via the existing `typeLabel()` helper) and `detail` text. When `supporting_evidence` is empty, the "Key findings" label and list are omitted entirely (same conditional-render behavior as today's `detail.supporting_evidence.length > 0` guard) — the confidence number and reason still render.

The Drawer header's existing inline "· confidence 0.78" text is removed — it would otherwise duplicate the hero's percentage, in a different format, immediately below it.

Advisory recommendations (no writable policy — `detail.current_policy`/`proposed_policy` absent) still render the full hero: confidence, reason, and evidence exist independent of whether there's a policy change to display. Only the (already-conditional) policy-diff section itself stays hidden for advisory rows, exactly as today.

## 6. Shared `confidenceTier()` function

A new pure function (e.g. `lib/confidenceTier.ts`), signature `confidenceTier(score: number): "high" | "medium" | "low"`, implementing the §3 boundaries (`>= 0.8` → high, `>= 0.5` → medium, else low). Both the QueueTable badge and the Drawer hero call this same function for their color/label decision — no duplicated threshold logic.

## 7. New component

A new small presentational component (e.g. `components/ConfidenceHero.tsx`) encapsulates the hero card's rendering (confidence number + reason + findings list), taking `reason: string`, `confidenceScore: number`, and `evidence: EvidenceView[]` as props. `DetailPanel.tsx` composes it rather than growing further inline — the file is already sizeable and this keeps the new unit independently testable.

## 8. Testing

- `confidenceTier()`: direct unit tests at and around each boundary (0.79/0.8/0.81, 0.49/0.5/0.51) plus the extremes (0, 1).
- The new violet token pair: added to `tokens.contrast.test.ts`'s existing tiered-pair matrix (both light and dark schemes), same AAA(7:1) tier as the other tier-badge foregrounds.
- `ConfidenceHero`: renders the percentage, reason, and evidence list from props; a snapshot-free assertion-based test per tier (high/medium/low) confirming the right tier class/token is applied.
- `DetailPanel`/`QueueTable`: existing tests updated for the new section order (Drawer) and badge markup (QueueTable) — behavior (click handlers, disabled states) is unchanged, only presentation.
- Per this repo's established practice for UI-visible changes: live-verify in a browser (not just the automated suite) before considering the phase complete — Phase 1 caught a real, test-invisible CSS bug this way.

## 9. Out of scope, tracked for later phases

Phases 3–4 (table rank-badges + tab count-badges + remaining table/badge conventions; NavRail → top-nav shell) are separate design/plan/build cycles, sequenced after this one lands. Icon-per-evidence-kind (§2) is also deferred, pending a closed enum for evidence `kind`.
