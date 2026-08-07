"""Bridge: read a nightly-extract output directory (the 21 ``<domain>.json`` files + a
``manifest.json``) and seed the engine's stores, so the recommendation engine runs on REAL
eMRO extract data in a shadow-mode dry run — no AWS, no Oracle, no Spark.

The transforms here are the reference logic that later promotes into the Feature-Store
Glue jobs. Extract rows use lowercased eMRO column aliases (e.g. ``hostpartid``,
``onhandnew``); values are strings (the extract coerces Decimal/date to text).

Supply-cycle rows retain their purchase (``NEW``) versus repair (``REP``) lane and
vendor. A deterministic ``DEFAULT`` part/condition aggregate remains as the
backward-compatible lookup when no open order selects a vendor. AOG and projected
repair returns stay empty stubs; descriptive repair-cycle evidence creates no supply.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from trax_io_feature_store import InMemoryFeatureStore
from trax_io_feature_store.demand import demand_observation_window
from trax_io_feature_store.schemas import (
    Criticality,
    CurrentPolicy,
    DemandHistory,
    DemandObservation,
    InterchangeableGraph,
    InterchangeEdge,
    LeadTimeDistribution,
    LocationGraph,
    LocationNode,
    OpenOrder,
    OpenOrdersSnapshot,
    PartAttributes,
    RequisitionLine,
    RequisitionSnapshot,
    StockPosition,
    VendorEconomics,
)

from trax_io_reco.contracts.context import ScheduledDemandItem
from trax_io_reco.contracts.enums import EvidenceKind
from trax_io_reco.data.inventory_state import InMemoryInventoryState

_LOG = logging.getLogger("trax_io.reco.extract_loader")
_CANONICAL_VENDOR = "DEFAULT"
_DEFAULT_TIER = 4

# Default essentiality-code -> canonical 1..5 tier map (tenant-overridable, spec §4.3).
_DEFAULT_ESSENTIALITY_MAP: dict[str, int] = {
    "1": 1,
    "AOG": 1,
    "NG": 1,
    "NOGO": 1,
    "NO-GO": 1,
    "NO_GO": 1,
    "2": 2,
    "GO-IF": 2,
    "GOIF": 2,
    "GO_IF": 2,
    "3": 3,
    "DISPATCH": 3,
    "4": 4,
    "ROUTINE": 4,
    "5": 5,
    "CONSUMABLE": 5,
    "NON-CRITICAL": 5,
}

_REQUIRED_DOMAINS = ("stock_amount", "stock_level_upload", "part_master")

# Real eMRO ``hostparttypeid`` (+ legacy short) codes -> the feature-store's part_class
# Literal. Unknown codes fall back to None rather than a guessed value (design §4.3: hard
# constraints must never be silently fabricated).
_PART_CLASS_MAP: dict[str, str] = {
    "XPENDBL": "expendable",
    "EXPENDABLE": "expendable",
    "EXP": "expendable",
    "ROTABLE": "rotable",
    "ROT": "rotable",
    "SER": "rotable",
    "TOOL-SER": "rotable",
    "REPSER": "rotable",
    "REPAIRABLE": "repairable",
    "REP": "repairable",
    "NON-SER": "repairable",
    "REP-FA": "repairable",
    "CONSUMABLE": "consumable",
    "CONS": "consumable",
    "CON-RAW": "consumable",
    "GEN-CON": "consumable",
}


# --------------------------------------------------------------------------- #
# value coercion helpers (extract values are strings)
# --------------------------------------------------------------------------- #
def _i(v: Any, default: int = 0) -> int:
    if v is None or v == "":
        return default
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return default
    return int(round(f)) if math.isfinite(f) else default


def _f(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return default
    return f if math.isfinite(f) else default


def _dec(v: Any, default: str = "0") -> Decimal:
    if v is None or v == "":
        return Decimal(default)
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return Decimal(default)
    return d if d.is_finite() else Decimal(default)


def _parse_date(v: Any) -> date | None:
    """Parse an extract date string. Honors ISO and the Oracle-native MM/dd/yyyy[ HH:MM]
    forms (the extract may emit either depending on the driver — kept in lockstep with the
    feature-store demand_history Glue job)."""
    if not v:
        return None
    s = str(v).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[: len(fmt) + 6], fmt).date()
        except ValueError:
            continue
    return None


def _parse_datetime(v: Any) -> datetime | None:
    """Parse lifecycle timestamps without inventing a missing repair age."""

    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, datetime.min.time())
    if not v:
        return None
    raw = str(v).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in (
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _month_start(v: Any) -> date | None:
    d = _parse_date(v)
    return d.replace(day=1) if d else None


def _truthy(v: Any) -> bool:
    return str(v).strip().upper() in {"Y", "YES", "TRUE", "1"}


def _s(v: Any) -> str | None:
    """Coerce an extract value to the string a schema field expects, tolerating None.

    Real eMRO sometimes returns numeric-typed columns (e.g. ``atachapter`` as int ``0``)
    where the feature-store schema declares a ``str | None`` field. ``None``/``""`` stay
    ``None``; everything else is stringified (``str(0)`` -> ``"0"``, not dropped)."""
    if v is None or v == "":
        return None
    return str(v)


def _load(extract_dir: Path, domain: str) -> list[dict[str, Any]]:
    """Load + lowercase one domain's rows. Tolerant of a missing/corrupt/odd-shaped file —
    a single bad optional domain must not sink the whole shadow run."""
    path = extract_dir / f"{domain}.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text() or "[]")
    except (json.JSONDecodeError, OSError):
        _LOG.warning("skipping unreadable extract domain file: %s", path)
        return []
    if not isinstance(raw, list):
        return []
    return [{str(k).lower(): val for k, val in row.items()} for row in raw if isinstance(row, dict)]


def _domain_succeeded(manifest: dict[str, Any], extract_dir: Path, domain: str) -> bool:
    """Whether a source domain is known to have completed successfully.

    A manifest artifact is authoritative when present, including a failed status even
    if a stale file remains on disk. Legacy/canonical manifests that do not describe
    this domain retain the historical file-presence fallback.
    """

    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, list):
        matching = [
            artifact
            for artifact in artifacts
            if isinstance(artifact, dict) and artifact.get("domain") == domain
        ]
        if matching:
            # Duplicate/conflicting metadata fails closed.
            return all(artifact.get("status") == "succeeded" for artifact in matching)
    return (extract_dir / f"{domain}.json").exists()


def _load_strict_feed(
    extract_dir: Path,
    domain: str,
) -> list[dict[str, Any]]:
    """Load a manifest-trusted feed without converting corruption into emptiness."""

    path = extract_dir / f"{domain}.json"
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"{domain} artifact is missing or unreadable: {path}") from exc
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        raise ValueError(f"{domain} artifact must be a JSON array of objects: {path}")
    return [{str(key).lower(): value for key, value in row.items()} for row in raw]


# --------------------------------------------------------------------------- #
# main bridge
# --------------------------------------------------------------------------- #
def build_stores_from_extract(
    extract_dir: str | Path,
    *,
    tenant_id: str | None = None,
    essentiality_map: dict[str, int] | None = None,
    pool_by_part: bool = False,
) -> tuple[InMemoryFeatureStore, InMemoryInventoryState, str, list[tuple[str, str]]]:
    """Seed an InMemoryFeatureStore + InMemoryInventoryState from a local extract dir.

    Returns ``(fs, inv, tenant_id, keys)`` — the same contract as ``demo_loader.build_stores``,
    so ``RecommendationService`` and the CLI consume it unchanged.

    ``pool_by_part`` (default ``False``, byte-identical to the legacy behavior when off) opts
    into **network pooling**: real eMRO separates PLANNING locations (``stock_level_upload``
    / ``PN_INVENTORY_LEVEL.LOCATION`` — where ROP/EOQ policy lives) from PHYSICAL stocking
    locations (``stock_amount`` / ``PN_INVENTORY_DETAIL.LOCATION``). When enabled, on-hand
    (and its components) for a planning key ``(pn, planning_loc)`` becomes the SUM of that
    PN's stock across ALL physical locations, and demand history is pooled across all
    physical locations for that PN. Policy stays keyed per ``(pn, planning_loc)`` as today.
    """
    extract_dir = Path(extract_dir)
    missing = [d for d in _REQUIRED_DOMAINS if not (extract_dir / f"{d}.json").exists()]
    if missing:
        raise FileNotFoundError(
            f"extract dir {extract_dir} is missing required domain file(s): {missing} "
            "(a failed/partial extract must not be read as an empty inventory)"
        )
    manifest_path = extract_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        _LOG.warning("corrupt manifest at %s; falling back to defaults", manifest_path)
        manifest = {}
    failed_required = [
        domain
        for domain in _REQUIRED_DOMAINS
        if not _domain_succeeded(manifest, extract_dir, domain)
    ]
    if failed_required:
        raise ValueError(
            "extract manifest does not mark required domain(s) succeeded: "
            f"{failed_required}"
        )
    tenant_id = tenant_id or manifest.get("tenant_id") or "tenant"
    extract_date = _parse_date(manifest.get("extract_date")) or date(2026, 4, 1)
    emap = essentiality_map or _DEFAULT_ESSENTIALITY_MAP
    demand_available = all(
        _domain_succeeded(manifest, extract_dir, domain)
        for domain in (
            "demand_history_rotables",
            "demand_history_expendables",
        )
    )
    demand_window = demand_observation_window(manifest) if demand_available else None
    demand_event_source = "observed" if demand_available else "unavailable"
    open_orders_available = _domain_succeeded(manifest, extract_dir, "order_plan")
    price_available = _domain_succeeded(manifest, extract_dir, "pn_vendor_price")
    closed_orders_available = _domain_succeeded(
        manifest,
        extract_dir,
        "order_plan_closed_orders",
    )
    requisitions_available = _domain_succeeded(
        manifest,
        extract_dir,
        "order_plan_data_requisition",
    )

    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    rows = {
        domain: _load_strict_feed(extract_dir, domain)
        for domain in _REQUIRED_DOMAINS
    }
    rows.update(
        {
            domain: _load(extract_dir, domain)
            for domain in (
                "part_criticality",
                "location_master",
                "part_chain_details",
                "events",
            )
        }
    )
    rows["pn_vendor_price"] = (
        _load_strict_feed(extract_dir, "pn_vendor_price") if price_available else []
    )
    rows["order_plan_closed_orders"] = (
        _load_strict_feed(extract_dir, "order_plan_closed_orders")
        if closed_orders_available
        else []
    )
    for domain in (
        "demand_history_rotables",
        "demand_history_expendables",
    ):
        rows[domain] = (
            _load_strict_feed(extract_dir, domain) if demand_available else []
        )
    rows["order_plan"] = (
        _load_strict_feed(extract_dir, "order_plan") if open_orders_available else []
    )
    rows["order_plan_data_requisition"] = (
        _load_strict_feed(extract_dir, "order_plan_data_requisition")
        if requisitions_available
        else []
    )

    keys: set[tuple[str, str]] = set()

    # (a) stock_position  <- stock_amount #18  (correctly aliased)
    stock_by_key: dict[tuple[str, str], StockPosition] = {}
    for r in rows["stock_amount"]:
        pn, loc = r.get("hostpartid"), r.get("hostlocid")
        if not pn or not loc:
            continue
        serviceable = _i(r.get("onhandnew"))
        in_repair = _i(r.get("inrepair"))
        pos = StockPosition(
            tenant_id=tenant_id,
            pn=pn,
            location=loc,
            on_hand=serviceable + _i(r.get("onhandbad")) + in_repair,
            serviceable=serviceable,
            unserviceable_in_repair=in_repair,
            allocated_reserved=_i(r.get("allocated")),
            rental=_i(r.get("rentalqty")),
            loan=_i(r.get("loanqty")),
            extract_date=extract_date,
        )
        stock_by_key[(pn, loc)] = pos
        if not pool_by_part:
            fs.seed(tenant_id, "stock_position", (pn, loc), pos)
            keys.add((pn, loc))

    # (b) current_policy  <- stock_level_upload #19  (alias corrected at source)
    planning_keys: set[tuple[str, str]] = set()
    for r in rows["stock_level_upload"]:
        pn, loc = r.get("hostpartid"), r.get("hostlocid")
        if not pn or not loc:
            continue
        rop, max_stock = _i(r.get("rop")), _i(r.get("stockmax"))
        if pool_by_part and rop <= 0 and max_stock <= 0:
            # Planning-active guard (W3-5, defense in depth behind the extract-side
            # predicate): pooled real-eMRO runs key the whole universe off this domain,
            # and a fat extract carries every location row of each scoped part — zero-
            # policy rows must not become planning keys (984,021 seen vs the true 62,492).
            continue
        fs.seed(
            tenant_id,
            "current_policy",
            (pn, loc),
            CurrentPolicy(
                tenant_id=tenant_id,
                pn=pn,
                location=loc,
                rop=rop,
                eoq=_i(r.get("eoq")),
                safety_stock=_i(r.get("safetylevel")),
                max_stock=max_stock,
                replenishment_lead_days=_f(r.get("slreplenishmentlength")),
                extract_date=extract_date,
            ),
        )
        planning_keys.add((pn, loc))

    if pool_by_part:
        # Network pooling (opt-in): sum each PN's stock across ALL physical locations, then
        # assign that PN-network total to every planning key (pn, planning_loc).
        network_totals: dict[str, dict[str, int]] = defaultdict(
            lambda: {
                "on_hand": 0,
                "serviceable": 0,
                "unserviceable_in_repair": 0,
                "allocated_reserved": 0,
                "rental": 0,
                "loan": 0,
            }
        )
        for (pn, _loc), pos in stock_by_key.items():
            totals = network_totals[pn]
            totals["on_hand"] += pos.on_hand
            totals["serviceable"] += pos.serviceable
            totals["unserviceable_in_repair"] += pos.unserviceable_in_repair
            totals["allocated_reserved"] += pos.allocated_reserved
            totals["rental"] += pos.rental
            totals["loan"] += pos.loan
        for pn, loc in planning_keys:
            totals = network_totals.get(pn)
            if totals is None:
                continue
            fs.seed(
                tenant_id,
                "stock_position",
                (pn, loc),
                StockPosition(
                    tenant_id=tenant_id, pn=pn, location=loc, extract_date=extract_date, **totals
                ),
            )
            keys.add((pn, loc))

    # (c) part_attributes + criticality  <- part_master #15 (+ part_criticality #12)
    for r in rows["part_master"]:
        pn = r.get("hostpartid")
        if not pn:
            continue
        fs.seed(
            tenant_id,
            "part_attributes",
            (pn,),
            PartAttributes(
                tenant_id=tenant_id,
                pn=pn,
                description=r.get("partdescription") or r.get("partname"),
                ata_chapter=_s(r.get("atachapter")),
                part_class=_part_class(r),
                shelf_life_days=_i(r.get("shelflife")) or None,
                hazardous_material=_truthy(r.get("hazmat")),
                tool_control_item=_truthy(r.get("tool")),
                fleet_effectivity_tail_count=_i(r.get("nooftails")) or None,
                extract_date=extract_date,
            ),
        )
        raw_code = str(r.get("hostpartcriticalid") or "").strip()
        fs.seed(
            tenant_id,
            "criticality",
            (pn,),
            Criticality(
                tenant_id=tenant_id,
                pn=pn,
                raw_essentiality_code=raw_code or "0",
                canonical_tier=emap.get(raw_code.upper(), _DEFAULT_TIER),  # type: ignore[arg-type]
                extract_date=extract_date,
            ),
        )

    # (d) procurement vendor economics + independent NEW/REP supply-cycle distributions.
    pm_by_pn = {r.get("hostpartid"): r for r in rows["part_master"]}
    procurement_prices = _best_price_rows(
        rows["pn_vendor_price"],
        lane="NEW",
        by_vendor=True,
    )
    for r in procurement_prices:
        pn = str(r["hostpartid"])
        vendor = _vendor(r)
        pm = pm_by_pn.get(pn, {})
        fs.seed(
            tenant_id,
            "vendor_economics",
            (pn, vendor),
            VendorEconomics(
                tenant_id=tenant_id,
                pn=pn,
                vendor=vendor,
                unit_cost=_dec(r.get("price")),
                market_value_unit_cost=_dec(pm["marketunitcost"])
                if pm.get("marketunitcost")
                else None,
                average_cost=_dec(pm["averagecost"]) if pm.get("averagecost") else None,
                repair_cost_24mo_avg=_dec(pm["repaircost"]) if pm.get("repaircost") else None,
                minimum_order_qty=max(1, _i(r.get("minoq"), 1)),
                extract_date=extract_date,
            ),
        )
    # Preserve the legacy canonical lookup with the best procurement row per PN.
    for r in _best_price_rows(rows["pn_vendor_price"], lane="NEW", by_vendor=False):
        pn = str(r["hostpartid"])
        pm = pm_by_pn.get(pn, {})
        fs.seed(
            tenant_id,
            "vendor_economics",
            (pn, _CANONICAL_VENDOR),
            VendorEconomics(
                tenant_id=tenant_id,
                pn=pn,
                vendor=_CANONICAL_VENDOR,
                unit_cost=_dec(r.get("price")),
                market_value_unit_cost=_dec(pm["marketunitcost"])
                if pm.get("marketunitcost")
                else None,
                average_cost=_dec(pm["averagecost"]) if pm.get("averagecost") else None,
                repair_cost_24mo_avg=_dec(pm["repaircost"]) if pm.get("repaircost") else None,
                minimum_order_qty=max(1, _i(r.get("minoq"), 1)),
                extract_date=extract_date,
            ),
        )
    _seed_supply_cycle_distributions(
        fs,
        tenant_id=tenant_id,
        price_rows=rows["pn_vendor_price"],
        closed_rows=rows["order_plan_closed_orders"],
        extract_date=extract_date,
    )

    # (e) demand_history  <- demand_history_rotables #2 (removals) + _expendables #3 (issues)
    buckets: dict[tuple[str, str], dict[date, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0, 0, 0])
    )
    if demand_available:
        for r in rows["demand_history_rotables"]:  # rotable qty is a literal 1 in the SQL
            _bucket(buckets, r, idx=0, qty=_i(r.get("historyamount"), 1) or 1)
        for r in rows["demand_history_expendables"]:  # 0 -> dropped (no phantom demand)
            _bucket(buckets, r, idx=1, qty=_i(r.get("historyamount")))

    def _demand_observations(months: dict[date, list[int]]) -> list[DemandObservation]:
        observations = [
            DemandObservation(
                bucket="month",
                period_start=month,
                removals=values[0],
                issues=values[1],
                removal_events=values[2],
                issue_events=values[3],
            )
            for month, values in sorted(months.items())
        ]
        if not observations and demand_window is not None:
            # Match the Iceberg/Glue zero-demand representation: one explicit
            # zero marker retains the configured interval for a genuine stock
            # key while the basis calculator counts it as zero-filled.
            observations.append(
                DemandObservation(
                    bucket="month",
                    period_start=demand_window[0],
                    removals=0,
                    issues=0,
                    removal_events=0,
                    issue_events=0,
                )
            )
        return observations

    if pool_by_part:
        # Network pooling (opt-in): pool all physical locations' observations into one
        # per-PN series (sum removals/issues per shared bucket; union distinct buckets),
        # then assign the pooled series to every planning key (pn, planning_loc) for that PN.
        pooled_by_pn: dict[str, dict[date, list[int]]] = defaultdict(
            lambda: defaultdict(lambda: [0, 0, 0, 0])
        )
        for (pn, _loc), months in buckets.items():
            pn_months = pooled_by_pn[pn]
            for m, v in months.items():
                for i, value in enumerate(v):
                    pn_months[m][i] += value
        for pn, loc in planning_keys:
            months = pooled_by_pn.get(pn, {})
            obs = _demand_observations(months)
            fs.seed(
                tenant_id,
                "demand_history",
                (pn, loc),
                DemandHistory(
                    tenant_id=tenant_id,
                    pn=pn,
                    location=loc,
                    observation_start=demand_window[0] if demand_window else None,
                    observation_end=demand_window[1] if demand_window else None,
                    bucket="month",
                    event_count_source=demand_event_source,
                    observations=obs,
                    extract_date=extract_date,
                ),
            )
    else:
        for (pn, loc), months in buckets.items():
            obs = _demand_observations(months)
            fs.seed(
                tenant_id,
                "demand_history",
                (pn, loc),
                DemandHistory(
                    tenant_id=tenant_id,
                    pn=pn,
                    location=loc,
                    observation_start=demand_window[0] if demand_window else None,
                    observation_end=demand_window[1] if demand_window else None,
                    bucket="month",
                    event_count_source=demand_event_source,
                    observations=obs,
                    extract_date=extract_date,
                ),
            )
        # Ensure every stock key has a demand_history row (empty -> ultra_rare).
        for pn, loc in keys:
            if (pn, loc) not in buckets:
                fs.seed(
                    tenant_id,
                    "demand_history",
                    (pn, loc),
                    DemandHistory(
                        tenant_id=tenant_id,
                        pn=pn,
                        location=loc,
                        observation_start=demand_window[0] if demand_window else None,
                        observation_end=demand_window[1] if demand_window else None,
                        bucket="month",
                        event_count_source=demand_event_source,
                        observations=_demand_observations({}),
                        extract_date=extract_date,
                    ),
                )

    # (f) location_graph  <- location_master #5   (optional)
    for r in rows["location_master"]:
        loc = r.get("hostlocid")
        if not loc:
            continue
        main = r.get("hostparentlocid")
        fs.seed(
            tenant_id,
            "location_graph",
            (loc,),
            LocationGraph(
                tenant_id=tenant_id,
                location=loc,
                extract_date=extract_date,
                node=LocationNode(
                    location=loc,
                    related_main_warehouse=main or None,
                    role=("outstation" if main and main != loc else "main"),
                ),
            ),
        )

    # (g) open_orders_snapshot  <- order_plan #8 (OPEN)   (optional)
    if open_orders_available:
        open_by_key: dict[tuple[str, str], list[OpenOrder]] = defaultdict(list)
        unclassified_order_rows = 0
        for r in rows["order_plan"]:
            pn, loc = r.get("hostpartid"), r.get("hostlocid")
            if not pn or not loc:
                continue
            qty_open = max(0, _i(r.get("planquantity")) - _i(r.get("receivedquantity")))
            if qty_open <= 0:
                continue
            otype = _open_order_type(r)
            if otype is None:
                unclassified_order_rows += 1
                continue
            status = str(r.get("orderstatus") or "OPEN").strip().upper()
            # Procurement contributes supply only while OPEN. Repair rows retain
            # every lifecycle state with remaining source quantity so the repair
            # pipeline can disclose terminal/ineligible exclusions explicitly.
            if otype == "PO" and status != "OPEN":
                continue
            open_by_key[(pn, loc)].append(
                OpenOrder(
                    order_id=str(r.get("hostorderid") or "?"),
                    order_type=otype,
                    vendor=_s(r.get("hostvendorlocid")),
                    qty_open=qty_open,
                    expected_rcv_date=_parse_date(r.get("planrcvdate")),
                    order_line_id=_s(r.get("orderlineid")),
                    opened_at=_parse_datetime(r.get("planorderdate")),
                    status=status,
                    serial_number=_s(r.get("serialnumber")),
                    shop=_s(r.get("hostshopid")),
                    location=_s(loc),
                )
            )
        if unclassified_order_rows:
            _LOG.warning(
                "excluded %d open-order row(s) with unclassified order type",
                unclassified_order_rows,
            )
        for pn, loc in keys | set(open_by_key):
            orders = open_by_key.get((pn, loc), [])
            fs.seed(
                tenant_id,
                "open_orders_snapshot",
                (pn, loc),
                OpenOrdersSnapshot(
                    tenant_id=tenant_id,
                    pn=pn,
                    location=loc,
                    snapshot_at=datetime.combine(extract_date, datetime.min.time()),
                    orders=orders,
                    total_open_qty=sum(o.qty_open for o in orders),
                    extract_date=extract_date,
                ),
            )

    # (i) requisition_snapshot <- order_plan_data_requisition #9 (OPEN)   (optional)
    if requisitions_available:
        req_by_key: dict[tuple[str, str], list[RequisitionLine]] = defaultdict(list)
        for r in rows["order_plan_data_requisition"]:
            pn, loc = r.get("hostpartid"), r.get("hostlocid")
            if not pn or not loc or str(r.get("orderstatus") or "").upper() != "OPEN":
                continue
            qty_needed = max(0, _i(r.get("planquantity")) - _i(r.get("receivedquantity")))
            if qty_needed <= 0:
                continue
            req_by_key[(pn, loc)].append(
                RequisitionLine(
                    requisition_id=str(r.get("hostorderid") or "?"),
                    qty_needed=qty_needed,
                    need_by=_parse_date(r.get("planrcvdate")),
                    alt_source_location=r.get("hostreplsourcelocid") or None,
                )
            )
        for pn, loc in keys | set(req_by_key):
            lines = req_by_key.get((pn, loc), [])
            fs.seed(
                tenant_id,
                "requisition_snapshot",
                (pn, loc),
                RequisitionSnapshot(
                    tenant_id=tenant_id,
                    pn=pn,
                    location=loc,
                    snapshot_at=datetime.combine(extract_date, datetime.min.time()),
                    lines=lines,
                    total_qty_needed=sum(rl.qty_needed for rl in lines),
                    extract_date=extract_date,
                ),
            )
            # Dated, still-open requisitions are known future demand. Undated
            # lines remain visible in RequisitionSnapshot but cannot be placed
            # into a requested horizon and are deliberately excluded here.
            scheduled = tuple(
                ScheduledDemandItem(
                    due_date=line.need_by,
                    qty=line.qty_needed,
                    source_ref=line.requisition_id,
                    source_kind=EvidenceKind.REQUISITION,
                )
                for line in lines
                if line.need_by is not None
            )
            # Seed even an empty tuple: presence is the observed-empty signal.
            inv.seed(tenant_id, "scheduled_demand", (pn, loc), scheduled)

    # (h) interchangeable_graph  <- part_chain_details #11   (optional)
    _seed_interchange(fs, tenant_id, rows["part_chain_details"], extract_date)

    return fs, inv, tenant_id, sorted(keys)


# --------------------------------------------------------------------------- #
# transform helpers
# --------------------------------------------------------------------------- #
def _part_class(r: dict[str, Any]) -> str | None:
    # Real eMRO part_master carries hostparttypeid (e.g. "XPENDBL"); prefer it when present
    # since it's the system-of-record classification. The sample extract omits this column,
    # so the legacy flag-derived heuristic below is preserved as the fallback.
    raw_type = str(r.get("hostparttypeid") or "").strip().upper()
    if raw_type:
        return _PART_CLASS_MAP.get(raw_type)
    if _truthy(r.get("ispartkit")):
        return "rotable"
    if _truthy(r.get("partserializable")) or _truthy(r.get("partrepairable")):
        return "repairable"
    return "expendable"


def _vendor(row: dict[str, Any]) -> str:
    return str(row.get("hostvendorlocid") or "").strip() or _CANONICAL_VENDOR


def _price_lane(row: dict[str, Any]) -> str | None:
    """Classify one configured price without ever treating RO/REP as procurement."""

    explicit = str(row.get("ordertypeid") or "").strip().upper()
    if explicit:
        return {"PO": "NEW", "RO": "REP"}.get(explicit)
    condition = str(row.get("condition") or "").strip().upper()
    if condition in {"REP", "RO"}:
        return "REP"
    if condition in {"", "NEW", "SV", "OH", "AR", "USED", "PO"}:
        # Empty condition is the backward-compatible meaning of the legacy
        # procurement price feed. Explicit RO above remains authoritative.
        return "NEW"
    return None


def _price_classification_source(row: dict[str, Any]) -> str:
    """Describe how a configured price row was assigned to a supply lane."""

    if str(row.get("ordertypeid") or "").strip():
        return "explicit_order_type"
    if str(row.get("condition") or "").strip():
        return "configured_condition"
    return "legacy_default_new"


def _open_order_type(row: dict[str, Any]) -> str | None:
    """Classify open supply without turning missing/unknown types into PO credit.

    Explicit ``OrderTypeID`` is authoritative. Legacy rows may fall back only
    when every available order identity agrees on one delimiter-bounded PO/RO
    prefix; ambiguous or unclassified rows stay excluded.
    """

    explicit = str(row.get("ordertypeid") or "").strip().upper()
    if explicit:
        return explicit if explicit in {"PO", "RO"} else None

    prefixes: set[str] = set()
    for key in ("hostorderid", "orderid"):
        raw = str(row.get(key) or "").strip().upper()
        for prefix in ("PO", "RO"):
            if raw.startswith((f"{prefix}_", f"{prefix}-", f"{prefix}/")):
                prefixes.add(prefix)
    return prefixes.pop() if len(prefixes) == 1 else None


def _closed_lane(row: dict[str, Any]) -> tuple[str, str] | None:
    """Classify a closed order, preferring explicit OrderTypeID over legacy IDs."""

    explicit = str(row.get("ordertypeid") or "").strip().upper()
    if explicit:
        lane = {"PO": "NEW", "RO": "REP"}.get(explicit)
        return (lane, "explicit_order_type") if lane else None

    prefixes: set[str] = set()
    for key in ("hostorderid", "orderid"):
        raw = str(row.get(key) or "").strip().upper()
        for prefix in ("PO", "RO"):
            if raw.startswith((f"{prefix}_", f"{prefix}-", f"{prefix}/")):
                prefixes.add(prefix)
    if len(prefixes) != 1:
        return None
    prefix = prefixes.pop()
    return ("NEW" if prefix == "PO" else "REP", "legacy_order_id_prefix")


def _price_rank(row: dict[str, Any]) -> tuple[int, float, str]:
    return (
        0 if _truthy(row.get("preferred")) else 1,
        _f(row.get("price"), float("inf")),
        _vendor(row),
    )


def _best_price_rows(
    price_rows: list[dict[str, Any]],
    *,
    lane: str,
    by_vendor: bool,
) -> list[dict[str, Any]]:
    """Best configured row per PN/vendor or per PN for one explicit lane."""

    best: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in price_rows:
        pn = str(row.get("hostpartid") or "").strip()
        if not pn or _price_lane(row) != lane:
            continue
        key = (pn, _vendor(row)) if by_vendor else (pn,)
        incumbent = best.get(key)
        if incumbent is None or _price_rank(row) < _price_rank(incumbent):
            best[key] = row
    return [best[key] for key in sorted(best)]


def _bucket(buckets, r, *, idx: int, qty: int) -> None:
    pn, loc = r.get("hostpartid"), r.get("hostlocid")
    month = _month_start(r.get("historybegdate"))
    if not pn or not loc or month is None or qty <= 0:
        return
    buckets[(pn, loc)][month][idx] += qty
    buckets[(pn, loc)][month][idx + 2] += 1


def _configured_days(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    promised = _f(row.get("processinglength"), -1.0)
    return promised if promised > 0 else None


def _confidence(n_observations: int) -> str:
    if n_observations >= 30:
        return "high"
    if n_observations >= 10:
        return "medium"
    return "low"


def _supply_cycle_distribution(
    *,
    tenant_id: str,
    pn: str,
    vendor: str,
    lane: str,
    cycles: list[tuple[int, str, date]],
    configured_price: dict[str, Any] | None,
    grouping_level: str,
    extract_date: date,
) -> LeadTimeDistribution | None:
    promised = _configured_days(configured_price)
    realized = sorted(days for days, _, _ in cycles)
    if realized:
        n = len(realized)
        mean = sum(realized) / n
        p50 = realized[n // 2]
        p90 = realized[min(n - 1, int(round(0.9 * (n - 1))))]
        p99 = realized[min(n - 1, int(round(0.99 * (n - 1))))]
        classification_source = (
            "legacy_order_id_prefix"
            if any(
                source == "legacy_order_id_prefix"
                for _, source, _ in cycles
            )
            else "explicit_order_type"
        )
        return LeadTimeDistribution(
            tenant_id=tenant_id,
            pn=pn,
            vendor=vendor,
            condition=lane,  # type: ignore[arg-type]
            promised_lead_days=promised,
            realized_mean_days=mean,
            realized_p50_days=p50,
            realized_p90_days=p90,
            realized_p99_days=p99,
            promised_vs_actual_delta_mean=(
                mean - promised if promised is not None else None
            ),
            n_observations=n,
            observed_cycle_days=tuple(realized),
            extract_date=extract_date,
            evidence_status="observed",
            source="order_plan_closed_orders",
            grouping_level=grouping_level,  # type: ignore[arg-type]
            confidence=_confidence(n),  # type: ignore[arg-type]
            data_cutoff=max(received for _, _, received in cycles),
            model_version="supply-cycle-v2",
            proxy_definition=(
                "order_creation_to_last_receipt" if lane == "REP" else None
            ),
            classification_source=classification_source,  # type: ignore[arg-type]
        )
    if promised is None:
        return None
    # A single configured promise is represented as a degenerate fallback,
    # not embellished with invented observed variance or sample coverage.
    return LeadTimeDistribution(
        tenant_id=tenant_id,
        pn=pn,
        vendor=vendor,
        condition=lane,  # type: ignore[arg-type]
        promised_lead_days=promised,
        realized_mean_days=promised,
        realized_p50_days=promised,
        realized_p90_days=promised,
        realized_p99_days=promised,
        promised_vs_actual_delta_mean=None,
        n_observations=0,
        observed_cycle_days=(),
        extract_date=extract_date,
        evidence_status="configured_fallback",
        source="pn_vendor_price",
        grouping_level=grouping_level,  # type: ignore[arg-type]
        confidence="low",
        data_cutoff=extract_date,
        model_version="supply-cycle-v2",
        proxy_definition="configured_repair_promise" if lane == "REP" else None,
        classification_source=_price_classification_source(configured_price),
    )


def _seed_supply_cycle_distributions(
    fs: InMemoryFeatureStore,
    *,
    tenant_id: str,
    price_rows: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
    extract_date: date,
) -> None:
    """Seed vendor-specific and backward-compatible part-level NEW/REP rows."""

    prices_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    part_prices: dict[tuple[str, str], dict[str, Any]] = {}
    for lane in ("NEW", "REP"):
        for row in _best_price_rows(price_rows, lane=lane, by_vendor=True):
            pn = str(row["hostpartid"])
            prices_by_key[(pn, _vendor(row), lane)] = row
        for row in _best_price_rows(price_rows, lane=lane, by_vendor=False):
            part_prices[(str(row["hostpartid"]), lane)] = row

    cycles_by_key: dict[
        tuple[str, str, str],
        list[tuple[int, str, date]],
    ] = defaultdict(list)
    part_cycles: dict[tuple[str, str], list[tuple[int, str, date]]] = defaultdict(list)
    for row in closed_rows:
        classified = _closed_lane(row)
        pn = str(row.get("hostpartid") or "").strip()
        ordered = _parse_date(row.get("planorderdate"))
        received = _parse_date(row.get("actualrcvdate"))
        if (
            classified is None
            or not pn
            or ordered is None
            or received is None
            or received < ordered
        ):
            continue
        lane, classification_source = classified
        cycle = ((received - ordered).days, classification_source, received)
        cycles_by_key[(pn, _vendor(row), lane)].append(cycle)
        part_cycles[(pn, lane)].append(cycle)

    for pn, vendor, lane in sorted(set(prices_by_key) | set(cycles_by_key)):
        distribution = _supply_cycle_distribution(
            tenant_id=tenant_id,
            pn=pn,
            vendor=vendor,
            lane=lane,
            cycles=cycles_by_key.get((pn, vendor, lane), []),
            configured_price=prices_by_key.get((pn, vendor, lane)),
            grouping_level="part_vendor_condition",
            extract_date=extract_date,
        )
        if distribution is not None:
            fs.seed(
                tenant_id,
                "lead_time_distribution",
                (pn, vendor, lane),
                distribution,
            )

    # DEFAULT remains a real part/condition aggregate, not a mislabeled
    # vendor-specific row. This keeps old snapshots and no-open-order assembly safe.
    for pn, lane in sorted(set(part_prices) | set(part_cycles)):
        distribution = _supply_cycle_distribution(
            tenant_id=tenant_id,
            pn=pn,
            vendor=_CANONICAL_VENDOR,
            lane=lane,
            cycles=part_cycles.get((pn, lane), []),
            configured_price=part_prices.get((pn, lane)),
            grouping_level="part_condition",
            extract_date=extract_date,
        )
        if distribution is not None:
            fs.seed(
                tenant_id,
                "lead_time_distribution",
                (pn, _CANONICAL_VENDOR, lane),
                distribution,
            )


def _seed_interchange(
    fs: InMemoryFeatureStore, tenant_id: str, detail_rows: list[dict[str, Any]], extract_date: date
) -> None:
    # Build undirected groups via union of (pn, parent) pairs; one_way from RelationType==1.
    edges_by_pn: dict[str, list[InterchangeEdge]] = defaultdict(list)
    members_by_pn: dict[str, set[str]] = defaultdict(set)
    for r in detail_rows:
        a, b = r.get("hostpartid"), r.get("hostchainparentid")
        if not a or not b or a == b:
            continue
        one_way = str(r.get("relationtype") or "0").strip() == "1"
        for head in (a, b):
            edges_by_pn[head].append(InterchangeEdge(from_pn=a, to_pn=b, one_way=one_way))
            members_by_pn[head].update({a, b})
    for pn, members in members_by_pn.items():
        group_id = "+".join(sorted(members))
        fs.seed(
            tenant_id,
            "interchangeable_graph",
            (pn,),
            InterchangeableGraph(
                tenant_id=tenant_id,
                pn=pn,
                group_id=group_id,
                members=sorted(members),
                edges=edges_by_pn[pn],
                extract_date=extract_date,
            ),
        )
