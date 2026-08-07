# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: airline spares/inventory planners — the frontline users who work the approval queue (Workbench) daily, review AI-generated recommendations, and act on reorder point (ROP), economic order quantity (EOQ), safety stock, and max changes for parts under their management.

Secondary: tenant owners/admins — manage billing (Stripe checkout/portal), team membership, and per-tenant governance (kill switch), but are not the daily operational user. Confirmed via role-gated nav (`owner`, `admin`, `planner`, `viewer` roles in `apps/web/src/App.tsx`).

## Product Purpose

Aeronta Inventory is an AI-driven inventory optimization layer for airline spares management. It continuously recomputes (ROP, EOQ, Safety Stock, Max) per part × location, and closes the loop by writing the result back into the customer's MRO system under policy-driven autonomy — not just surfacing a dashboard. Success means governed, auditable inventory decisions that reduce holding cost, ordering cost, and stockout/AOG risk versus the pre-agent baseline, with savings traceable to specific changes (the Business Value Report).

## Positioning

"Recommend, govern, and act" — a neighboring product could truthfully copy the recommend step (forecasting) but not the full loop: a tiered autonomy model that decides how much latitude each change gets, hard guardrails that are never bypassed (single-write delta caps, shelf-life/hazmat/tool-control clamps, active-AOG forcing the most conservative posture), a per-tenant kill switch, an append-only audit ledger, and Aeronta as the only agent in the pipeline with actual write permission into the MRO system (every other component is read-only). Native eMRO integration depth (the connector already exists) is a stated differentiator against generic supply-chain products retrofitted to airline MRO.

## Operating Context

- Customers are airline MRO operations managing spares inventory; the product reads from and writes back to the customer's MRO system (eMRO), a system of record it does not own.
- Airline spares are not general-purpose retail inventory: lead times swing with vendor/shipping mode, demand for many parts is intermittent by nature, and a shortage on the wrong part can ground an aircraft (AOG).
- Multi-tenant SaaS: self-serve signup with a 14-day free-trial (card required), tiered pricing by part-location key quota (starter/growth/scale) plus a negotiated Enterprise tier, alongside data onboarding via either a nightly Oracle extract/eMRO integration or self-serve CSV/xlsx upload.
- Daily workflow centers on the Workbench approval queue (server-paged, priority-sorted) plus supporting views: portfolio Overview, Part Drill-Down, AI Recommendations, Forecast & Service Levels, What-If Scenarios, Reports (Business Value Report), and Data & Connections.

## Capabilities and Constraints

- Every displayed number carries a provenance invariant — a value can't render without its lineage (source, system of record, freshness, coverage, confidence, whether it's derived) attached. This applies to all live operational data, but deliberately not to the Reports/BVR view, which is itself already a governed report rather than a live operational number.
- Writeback is the only agent action with actual MRO write permission; every other specialist/read path is read-only.
- Hard guardrails apply at every autonomy tier and are never bypassed: capped single-write deltas, shelf-life/hazmat/tool-control clamps, active AOG forcing the most conservative tier.
- Every writeback decision (approved, rejected, deferred, or automated) is recorded in an append-only audit ledger with a rollback path over a configurable window (default 90 days) — no writeback is a one-way door.
- A per-tenant kill switch can halt all writes instantly.
- SOC 2 Type II posture is a day-one product constraint, not retrofitted later (audit-log emission, tenant isolation, encryption).
- Tenant isolation is enforced at multiple layers (contract, agent, data, infra) — a product-level constraint any new feature must respect, not just an implementation detail.

## Brand Commitments

"Aeronta Inventory" is the customer-facing product/company brand: site title, app title, and footer copyright. It is a text wordmark — no logo/icon asset currently exists in the repo. "Trax IO" is the internal engineering codename used in project docs for the Trax-integrated build; it is not customer-facing and should not be used in user-facing surfaces.

## Evidence on Hand

- Marketing copy (`apps/site/src/pages/{index,product,pricing}.astro`) is real, shipped copy — not a placeholder — and can be treated as confirmed positioning language, not invented claims.
- Pricing tiers and dollar amounts are fetched live from Supabase (`plan_tiers`) at build time; no dollar figures are hardcoded, and none should be invented in future work.
- No customer testimonials, case studies, press, or logos exist yet — future work must not fabricate them.
- No visual logo/icon asset exists — only a text wordmark.

## Product Principles

1. Governed autonomy over blind automation — every write is explainable, capped, and reversible; trust is earned through the audit trail, not asserted.
2. Provenance is non-negotiable on operational data — a number without lineage is not shown.
3. Built for airline MRO reality, not generic supply chain — intermittent demand, AOG risk, and regulatory clamps (shelf-life/hazmat/tool-control) are first-class, not edge cases.
4. Value must be attributable — savings claims trace to specific changes (BVR), never asserted in the abstract.
5. Read-only by default — write access is a single, audited seam, not a general capability.

## Accessibility & Inclusion

WCAG 2.1 AA is an established product standard for `apps/web` (focus-visible rings, `aria-current` navigation, table header scoping, focus-trapped dialogs) — treat this as the accessibility floor for all future UI work, not an optional enhancement.
