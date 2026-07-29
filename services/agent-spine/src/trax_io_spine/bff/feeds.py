"""Slice S7 — Data & Connections / feed health (PRD §6.7).

The 13 spec `FeedId`s (DATA-MODEL.md §2), mapped to the REAL 21-domain nightly-extract
registry (`tools/nightly-extract/src/trax_io_extract/domains.py`) and what
`services/recommendation-engine/src/trax_io_reco/data/extract_loader.py` actually
consumes — not a spec-shaped fiction. Verified by direct inspection of both files
(not guessed): a feed is `CONNECTED` only if its extract domain(s) are both present
in the 21-domain registry AND read into a feature-store schema by the loader;
`PARTIAL` if extracted-but-unwired, or wired but structurally thin; `NOT_CONNECTED`
if no extract domain exists for it at all in v1.

Per-feed evidence (cross-checked against `extract_loader.py` line-by-line):

- INVENTORY — `stock_amount` (#18) + `stock_level_upload` (#19) build `StockPosition`/
  `CurrentPolicy` for every key; `part_master` (#15) backs `PartAttributes`. CONNECTED.
- PURCHASE_ORDERS — `order_plan` (#8, filtered to `orderstatus == "OPEN"`) +
  `order_plan_closed_orders` (#7, realized lead times) build `OpenOrdersSnapshot` /
  `LeadTimeDistribution`. CONNECTED.
- VENDOR_MASTER — `pn_vendor_price` (#16) builds `VendorEconomics`; the `vendor`
  master domain (#21) is extracted but never read by the loader, so it is NOT listed
  as a backing domain. CONNECTED on pn_vendor_price alone, with the caveat that every
  part collapses to one canonical `"DEFAULT"` vendor (`_CANONICAL_VENDOR`) — real
  per-vendor granularity is not modeled in v1.
- INTERCHANGEABILITY — `part_chain` (#10, unused by the loader directly) +
  `part_chain_details` (#11, read by `_seed_interchange`) build `InterchangeableGraph`.
  CONNECTED, with the design doc's own ~61% real-world coverage flagged as a known
  data-quality risk (not a v1 wiring gap).
- REQUISITIONS — `order_plan_data_requisition` (#9) builds `RequisitionSnapshot`.
  Dated open lines become `ScheduledDemandItem`s consumed by the planning horizon;
  undated lines remain visible in the snapshot but are excluded from horizon math.
  CONNECTED.
- SHELF_LIFE — `part_master` (#15) carries `PartAttributes.shelf_life_days`, a
  *duration*, not a lot-level expiry ledger (spec's `SHELF_LIFE` feed wants
  `partNumber, lot, expiryDate, base`). PARTIAL: real field, no lot/expiry tracking.
- FLEET_UTILIZATION — `causal_values` (#1) is extracted every run but
  `extract_loader.py` never reads it (not in the `rows` dict, no `CausalUtilization`
  construction anywhere in the bridge) — the feature-store schema exists as an
  unpopulated stub. PARTIAL: extracted, not consumed.
- MAINTENANCE_SCHEDULE remains unavailable: requisition-derived scheduled demand is
  not a forward-looking maintenance/check schedule.
- REPAIR_ORDERS — explicitly classified RO rows in
  `order_plan_closed_orders` now build an independent `REP` repair-cycle
  distribution. This remains an order-creation-to-last-receipt proxy until
  physical induction/serviceable-completion events are available. CONNECTED
  at the proxy boundary.
- SERIAL_TRACKING, RELIABILITY, QUOTATIONS, CONTRACTS — no domain among the
  21 backs any of these. NOT_CONNECTED.
"""

from __future__ import annotations

from dataclasses import dataclass

from trax_io_spine.bff.models import FeedConnectionStatus, FeedId


@dataclass(frozen=True, slots=True)
class FeedDefinition:
    feed_id: FeedId
    name: str
    status: FeedConnectionStatus
    domains: tuple[str, ...]
    notes: str


# Canonical order matches DATA-MODEL.md §2's FeedId table exactly.
FEED_DEFINITIONS: tuple[FeedDefinition, ...] = (
    FeedDefinition(
        FeedId.REQUISITIONS,
        "Requisitions / open demand",
        FeedConnectionStatus.CONNECTED,
        ("order_plan_data_requisition",),
        "Open requisition lines flow into RequisitionSnapshot; dated lines become "
        "scheduled demand used by planning. Undated lines remain visible but are "
        "excluded from requested-horizon demand because they have no due date.",
    ),
    FeedDefinition(
        FeedId.PURCHASE_ORDERS,
        "Purchase orders (on-order)",
        FeedConnectionStatus.CONNECTED,
        ("order_plan", "order_plan_closed_orders"),
        "Open PO/RO quantities and realized lead times both flow into the engine.",
    ),
    FeedDefinition(
        FeedId.QUOTATIONS,
        "Quotations (RFQ / on hand)",
        FeedConnectionStatus.NOT_CONNECTED,
        (),
        "No RFQ/quotation domain in the 21-domain extract. `order_plan*` domains are "
        "post-award orders, not pre-award RFQs — uncertain whether eMRO exposes this "
        "at all for this tenant.",
    ),
    FeedDefinition(
        FeedId.REPAIR_ORDERS,
        "Repair orders / cycle history",
        FeedConnectionStatus.CONNECTED,
        ("order_plan_closed_orders",),
        "Explicit RO rows build the independent REP distribution. Native rows and "
        "canonical repair-history uploads use the same feature contract. The current "
        "duration remains labeled an RO cycle-time proxy (order creation to last "
        "receipt), not physical induction-to-serviceable-completion TAT.",
    ),
    FeedDefinition(
        FeedId.INVENTORY,
        "Current inventory / on-hand",
        FeedConnectionStatus.CONNECTED,
        ("stock_amount", "stock_level_upload", "part_master"),
        "Strongest feed in v1 — on-hand, serviceable/in-repair, and current policy "
        "all derive from real per-(PN, Location) rows every run.",
    ),
    FeedDefinition(
        FeedId.SERIAL_TRACKING,
        "Serial / rotable tracking",
        FeedConnectionStatus.NOT_CONNECTED,
        (),
        "No domain tracks individual serials by status/location/time-since-overhaul; "
        "eMRO component-serial tables are not wired into the extract in v1.",
    ),
    FeedDefinition(
        FeedId.RELIABILITY,
        "Reliability (MTBUR/MTBF/removals)",
        FeedConnectionStatus.NOT_CONNECTED,
        (),
        "Demand-history domains give raw removal/issue counts, not reliability-"
        "engineering statistics (MTBUR/MTBF/scrap rate). No schema populates these.",
    ),
    FeedDefinition(
        FeedId.FLEET_UTILIZATION,
        "Fleet & utilization (FH/FC)",
        FeedConnectionStatus.PARTIAL,
        ("causal_values",),
        "Extracted every run (domain #1) but never read by the loader — the "
        "feature-store's CausalUtilization schema exists and is unpopulated.",
    ),
    FeedDefinition(
        FeedId.MAINTENANCE_SCHEDULE,
        "Maintenance schedule (checks)",
        FeedConnectionStatus.NOT_CONNECTED,
        (),
        "No domain pulls forward-looking check schedules. Dated requisitions now "
        "provide scheduled open demand, but they are not a maintenance/check schedule.",
    ),
    FeedDefinition(
        FeedId.VENDOR_MASTER,
        "Vendor master & lead times",
        FeedConnectionStatus.CONNECTED,
        ("pn_vendor_price",),
        "Vendor pricing/lead time flows into the engine via pn_vendor_price, but every "
        "part collapses to one canonical \"DEFAULT\" vendor in v1 — real multi-vendor "
        "economics are not modeled. The vendor master domain (#21) is extracted but "
        "never read by the loader.",
    ),
    FeedDefinition(
        FeedId.INTERCHANGEABILITY,
        "Interchangeability / alternates / PMA",
        FeedConnectionStatus.CONNECTED,
        ("part_chain", "part_chain_details"),
        "Interchangeable-part groups build from real chain data; design doc flags "
        "~61% real-world mapping coverage as a known data-quality risk.",
    ),
    FeedDefinition(
        FeedId.CONTRACTS,
        "Contracts (PBH / pooling / consignment)",
        FeedConnectionStatus.NOT_CONNECTED,
        (),
        "No PBH/pooling/consignment domain in the extract; likely lives in a "
        "separate commercial module outside eMRO's MRO tables, if it exists at all.",
    ),
    FeedDefinition(
        FeedId.SHELF_LIFE,
        "Shelf life / expiry",
        FeedConnectionStatus.PARTIAL,
        ("part_master",),
        "`part_master` carries a shelf-life duration field, but there is no "
        "lot-level expiry ledger (partNumber, lot, expiryDate, base) in v1.",
    ),
)

FEED_DEFINITIONS_BY_ID: dict[FeedId, FeedDefinition] = {
    d.feed_id: d for d in FEED_DEFINITIONS
}
