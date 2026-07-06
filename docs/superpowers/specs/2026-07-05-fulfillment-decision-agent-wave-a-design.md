# Sub-project #12 — Fulfillment-Path Decision Agent — Overview + Wave A Design

**Date:** 2026-07-05
**Status:** Approved (design)
**Scope:** This document covers (1) the overall sub-project framing and wave decomposition, and (2)
a fully-detailed design for **Wave A only** (requisition data wiring). Waves B–E are described at
decomposition level here and will each get their own brainstorm → spec → plan cycle when their turn
comes, in dependency order.

## 1. Context

The user shared a reference mockup (`dash_ai.html`) — an "eMRO Inventory Decision Agent" dashboard:
open requisitions routed to a fulfillment path (ISSUE from stock → TRANSFER from another base →
REPAIR via an open return order → BUY a new PO), an AI-generated "Executive Briefing" narrative, and
an "Action Queue" of drafted fulfillment actions.

This does not map onto anything Trax currently builds. Trax's live domain is PN×Location **policy**
recommendations (ROP/EOQ/Safety Stock/Max changes) — a fundamentally different question from routing
a single point-in-time requisition to a fulfillment source. The closest existing artifact is the
design doc's own backlog: **v5 — Repair-vs-Buy / Sourcing** (`ROADMAP.md` line 215-216), a
one-line stub: *"Sourcing specialist (PO / RO / interchange / rental / loan / pool-exchange /
cannibalization)"* — explicitly scoped for after v1, not concurrent with it.

The user chose to pull this forward as a properly-staged new sub-project rather than a UI prototype
or a pure restyle.

## 2. Sub-project framing

- New `ROADMAP.md` section: `### Sub-project #12 — Fulfillment-Path Decision Agent`, under a new
  **Wave 4** (placed after the existing Wave 3 — this is additional scope pulled forward from the
  v5 backlog, not part of the original v1 plan; the v5 backlog stub stays in place as a historical
  note of where this originated). Sub-project numbers #1–#11 are all in use with no gaps, so #12 is
  the natural next number.
- Target frontend: **`apps/web`** — the app now doing approve/reject/defer workflows (the
  Workbench), closest in spirit to an action queue and already carrying the visual language
  (dark theme, KPI cards, badges) the reference mockup uses.

**Key architectural distinction:** the fulfillment-path decision is **not** a `Recommendation` in
Trax's existing sense. Every current recommendation type (`PURCHASE`/`TRANSFER`/`REDUCE_SELL`/
`ADJUST_MIN_MAX`) changes a long-run *policy* (ROP/EOQ/Safety Stock/Max). This new concept routes a
single point-in-time *requisition* (a specific demand event with a quantity and a need-by date) to
its best fulfillment source. It needs its own object type — working name `FulfillmentDecision` —
rather than overloading the existing `Recommendation` contract. This distinction governs Wave B's
design and must not be blurred for convenience.

**Wave decomposition** (dependency order; only Wave A is detailed in this document):

- **Wave A — Foundation.** Wire the currently-unconsumed `order_plan_data_requisition` extract
  domain (#9) into a new feature-store schema, and expose it on the recommender context. Pure data
  plumbing — no decision logic. Detailed in §3-§7 below.
- **Wave B — Brain.** The `FulfillmentDecision` object and the actual ISSUE→TRANSFER→REPAIR→BUY
  ranking logic, consuming Wave A's data plus the already-existing `StockPosition`/
  `OpenOrdersSnapshot`/`TransferRecommender` donor-search logic. Priority classification
  (AOG/CRITICAL/URGENT/ROUTINE, derived from the existing `criticality` tier + `aog_signal` — the
  raw requisition extract has no priority field of its own) belongs here, not in Wave A.
- **Wave C — Loop.** A BFF surface + action queue for reviewing/approving `FulfillmentDecision`s,
  likely reusing much of the existing approve/reject/defer interaction pattern over a new object
  type.
- **Wave D — Narrative.** The AI-generated executive briefing. The reference mockup's own
  `narrative_source: "claude"` text is a hardcoded literal in its sample data, not a live API call
  in the mockup's runtime code — a useful signal that this should default to an **offline,
  precomputed-and-cached** narrative (generated when a decision batch finalizes, invalidated the
  same way the BVR report is memoized) rather than a live synchronous LLM call per dashboard load.
  This would be the first real LLM call in the project; the real-API-vs-template question stays
  open for Wave D's own dedicated brainstorm.
- **Wave E — Face.** The actual dashboard view in `apps/web`.

## 3. Wave A — grounded findings

Verified directly against source, not assumed:

- **Domain #9's real columns** (`tools/nightly-extract/sql/09_order_plan_data_requisition.sql`,
  source tables `Requisition_Header`/`requisition_detail`): requisition+line id (`HostOrderID`),
  requesting location (`HostLocID`), an alternate supply/repair-source location
  (`HostReplSourceLocID` ← `ASSIGN_TO`), PN (`HostPartID`), status (`OrderStatus`), need-by date
  (`PlanRcvDate` ← `REQUIRE_DATE`), quantity needed (`PlanQuantity`), quantity received
  (`ReceivedQuantity`), created date (`PlanOrderDate`). Filtered to `status = 'OPEN'`, excluding
  requisitions already converted to formal orders. **No priority field, no vendor field** in the
  raw extract.
- **Repair supply is mostly already visible.** Domain #8 (`order_plan`) is already wired and already
  builds `OpenOrdersSnapshot` with `order_type: Literal["PO", "RO"]` and `expected_rcv_date` —
  *already-open* repair orders are already visible through existing, unchanged code. Domain #9 adds
  only the **demand** side (what's being asked for); it does not touch repair supply at all.
- **Repair cost is already visible.** `VendorEconomics.repair_cost_24mo_avg` already exists, already
  seeded from `part_master`'s `repaircost` column. No new work needed for repair-path costing.
- **Repair TAT (turnaround time) has no data source anywhere.** `RepairTat` exists only as a
  permanently-zeroed stub on `PartLocationContext` (`mean_days=0.0, p90_days=0.0,
  n_observations=0`) — no feature-store schema, no extract domain feeds it, and none of the 21
  registered extract domains contains repair-order *close* events with actual turnaround times.
  **Consequence, explicitly accepted as a v1 limitation:** a requisition can only be routed to
  REPAIR when an open repair order *already exists* (visible via the existing `OpenOrdersSnapshot`
  RO entries) — proposing a brand-new repair as a fulfillment path is out of scope, since there is
  no data to estimate how long it would take. This limitation is a Wave B ranking-logic concern, but
  it is a direct consequence of Wave A's findings and must be documented alongside whatever Wave A
  ships.

## 4. Wave A scope

**In scope — pure data plumbing, no decision logic:**
- A new `RequisitionLine` + `RequisitionSnapshot` schema in
  `services/feature-store/src/trax_io_feature_store/schemas/features.py`, structured like the
  existing `OpenOrder`/`OpenOrdersSnapshot` pair (one aggregate snapshot per `(pn, location)` key,
  holding a list of individual requisition lines) — kept **deliberately separate** from
  `OpenOrdersSnapshot` rather than merged into it, since one represents demand and the other supply;
  conflating them would be a modeling mistake.
- Wiring domain #9 into `services/recommendation-engine/src/trax_io_reco/data/extract_loader.py`,
  following the exact pattern already used for domain #8's `OpenOrdersSnapshot` wiring: add
  `"order_plan_data_requisition"` to the loaded-domains tuple, aggregate rows by `(pn, location)`,
  filter to `OPEN` status, compute `qty_needed = max(0, plan_quantity - received_quantity)`
  (discarding fully-received lines), and seed the feature store.
- Exposing the new snapshot as a new optional field on `PartLocationContext`
  (`services/recommendation-engine/src/trax_io_reco/contracts/context.py`), matching the existing
  `open_orders: OpenOrdersSnapshot | None = None` pattern exactly — optional, so no existing
  recommender construction breaks.

**Explicitly out of scope for Wave A:**
- Any ranking, routing, or priority-classification logic (Wave B).
- Any new repair-TAT data sourcing (no viable extract source exists; documented as a standing v1
  limitation, not a gap this wave tries to close).
- Any BFF endpoint, frontend surface, or narrative generation (Waves C/D/E).

## 5. Testing

- New schema tests for `RequisitionLine`/`RequisitionSnapshot` — find and mirror whatever test
  pattern the existing `OpenOrder`/`OpenOrdersSnapshot` pair already has (exact file to be located
  and read directly during planning, not assumed).
- A new `extract_loader` test proving: domain #9 rows aggregate correctly into
  `RequisitionSnapshot` objects, `OPEN`-status filtering is applied, `qty_needed` math matches the
  `order_plan`/`OpenOrdersSnapshot` precedent (`max(0, plan_quantity - received_quantity)`,
  discarding non-positive results), and the snapshot is seeded into the feature store correctly —
  mirroring whatever test already exists for domain #8's wiring (to be located directly during
  planning).
- A `PartLocationContext` compatibility test confirming the new optional field doesn't break
  existing recommender construction (i.e., a context built without a `requisition` argument still
  constructs successfully, defaulting to `None`).

## 6. Out of scope, tracked for later

- Waves B (fulfillment-path ranking + priority derivation), C (action queue + BFF), D (AI
  narrative), E (frontend view) — each gets its own brainstorm → spec → plan cycle, in that order,
  once the prior wave has shipped.
- Closing the repair-TAT data gap for hypothetical new repairs — would require new eMRO extract
  queries against data that may not exist in the source system at all; a separate investigation,
  not assumed solvable within this sub-project's current scope.
