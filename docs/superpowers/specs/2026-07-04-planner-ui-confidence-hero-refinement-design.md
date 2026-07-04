# #7 Planner UI — ConfidenceHero Reference-Match Refinement — Design

**Date:** 2026-07-04
**Status:** Approved (design)
**Sub-project:** #7 Planner UI "Trax IO Review" — a refinement pass on the `ConfidenceHero` component shipped in Phase 2 of the 4-phase Airvoyant-inspired redesign, NOT one of the numbered phases itself. Phases 1–3 (theme, confidence & rationale, table/badge conventions) are shipped. Phase 4 (navigation shell) remains separate and untouched by this spec.
**Authoritative input:** a literal screenshot of the reference site's "AI Recommendation" hero card (materially more detailed than the text-described summary that drove Phase 2's original brainstorm), compared directly against the shipped `apps/planner-ui/src/components/ConfidenceHero.tsx` + `.module.css`.

## 1. Context

After Phase 2 shipped, the user shared an actual screenshot of the reference site's hero card, which is substantially richer than what drove the original Phase 2 brainstorm: a bordered card, an icon+title+subtitle header row with a decided-status readout, a gradient confidence percentage, an explicit "Why this recommendation?" heading, and a vendor/part/price/quantity/condition/lead-time/validity field grid.

Comparing against Trax's actual data model (`apps/planner-ui/src/api/types.ts`) surfaced a real constraint: the field grid needs data Trax's `RecommendationDetail` doesn't have (`vendor` exists only nested inside individual `OpenOrderView` entries, not as a top-level recommendation attribute; there is no `price`/`condition`/`validity` field anywhere). Replicating that grid would require new recommendation-engine data, not a frontend change. This was explicitly scoped out — see §2.

## 2. Scope

**In scope (pure visual/layout change over data Trax already has):**
- A bordered card wrapper around the hero, reusing the Drawer's existing `.panel` treatment.
- A header row: icon + "AI Recommendation" / "Powered by predictive analytics" (left), a decided-status badge (right, decided rows only).
- The confidence percentage becomes bigger/bolder with a tier-based two-color gradient (not flat), label stacked below instead of beside.
- A "Why this recommendation?" heading above the existing reason prose.

**Explicitly deferred (not in this pass):**
- The vendor/part/price/quantity/condition/lead-time/validity field grid — needs new recommendation-engine data, a materially different and bigger change than a frontend restyle. Revisit only if/when that data exists.
- Two-column "Key Findings" — the Drawer's fixed 420px width already made this cramped when evaluated during Phase 2's brainstorm; nothing about this pass changes that constraint, so the findings list stays single-column.
- Any change to the QueueTable confidence badge (Phase 3) — it stays flat-colored; the gradient is scoped to this large hero number only, not the small table badge.

## 3. Card structure

The hero `<section>` gains the exact border/radius treatment `DetailPanel.module.css`'s `.panel` already uses (`border: 0.5px solid var(--border); border-radius: 12px;`) rather than a new, one-off card style — this keeps it visually consistent with the Drawer's own established card language instead of reading as a foreign element. Only the border and radius are borrowed, not `.panel`'s background: the hero keeps its own `--surface-1` background (already shipped in Phase 2), so it reads as a distinct, nested card sitting inside the Drawer's outer `--surface-2` panel rather than blending into it.

## 4. Header row

A new row at the top of the hero card:
- **Left:** a small square icon tile (a `lucide-react` `Sparkles` icon — the library is already a dependency, no new package needed) using Trax's existing `--text-accent`/`--bg-accent` pair (the same "info/highlight" blue already used for icons elsewhere, e.g. `QueueTable`'s sort-direction icon) — a fixed, non-tier-dependent treatment, since this icon identifies "this is an AI-generated recommendation" generically, not a signal about this specific score. Next to it, two lines of text: **"AI Recommendation"** (bold) and **"Powered by predictive analytics"** (muted, smaller) — matching the reference's copy verbatim, since it's generic enough to fit Trax as-is.
- **Right:** a status badge, shown only when `detail.status !== "pending"`, reusing the exact classes `QueueTable.module.css` already defines for this purpose (`.status`, `.status_approved`, `.status_rejected`, `.status_deferred`) — no new color tokens, this is literally the same styling already shipped and verified for the Decided tab's status column, applied in a new location. Pending rows show nothing in this slot (mirroring how the reference's card only shows "Order Placed" once an order has actually been placed).

## 5. Confidence percentage

The percentage number becomes visually larger and bolder than Phase 2's shipped size, with the "confidence" label moved to its own line below the number (was inline beside it). The color becomes a two-color gradient instead of a flat tier color, applied via a text-clip gradient technique (`background: linear-gradient(...); background-clip: text; color: transparent;` or equivalent) — three separate gradients, one per tier, each staying within that tier's existing hue family so the high/medium/low trust signal Phase 2 established is preserved, not erased by a single fixed color regardless of score:

- **High** stays in the violet family (a lighter, blue-shifted violet paired with the existing violet, evoking the reference's blue-to-purple energy without abandoning the tier's identity).
- **Medium** stays neutral — a subtle two-tone neutral gradient (not a saturated color), preserving Phase 2's original reasoning that the "normal" middle case shouldn't visually compete with the two extremes that actually warrant attention.
- **Low** stays in the danger-red family (red paired with a deeper red or warm-shifted red).

Exact hex values for all 3 gradients × 2 endpoints × 2 schemes (12 new values) are a starting point here — computed and verified during implementation the same way every color in this app has been: both endpoints of every gradient must independently clear their tier's required WCAG threshold against the hero card's background (not just one endpoint, since a gradient's weakest point is what a user with low vision actually encounters at that point on the number). High and low stay at the existing AAA tier (7:1, matching the flat colors they replace); medium stays at AA (4.5:1), consistent with `--text-secondary`'s existing tier.

## 6. "Why this recommendation?" heading

A new bold heading directly above the existing `reason` prose paragraph, matching the reference's structure — the prose content itself is unchanged, this only adds a labeling heading above it.

## 7. Testing

- Component tests confirm: the card border class is applied; the header row renders the icon, title, and subtitle; the status badge appears only for non-pending statuses and shows the correct variant class per status; the confidence number's gradient class/style corresponds to the correct tier; the new heading renders above the reason text.
- All 6 new gradient endpoint colors (3 tiers × 2 endpoints, ×2 schemes = 12 values) get added to `tokens.contrast.test.ts`'s existing tiered-threshold harness (AAA for high/low, AA for medium), matching the exact pattern already used for every other color in this app.
- Per this project's established practice for UI-visible changes: live-verify in a browser (not just the automated suite) before considering this complete — every phase so far has caught at least one real, test-invisible bug this way.

## 8. Out of scope, tracked for later

The vendor/part/price/quantity/condition/lead-time/validity field grid remains deferred pending real recommendation-engine data. Phase 4 (navigation shell) is untouched and unrelated to this pass.
