"""Bridge: read a nightly-extract output directory (the 21 ``<domain>.json`` files + a
``manifest.json``) and seed the engine's stores, so the recommendation engine runs on REAL
eMRO extract data in a shadow-mode dry run — no AWS, no Oracle, no Spark.

The transforms here are the reference logic that later promotes into the Feature-Store
Glue jobs. Extract rows use lowercased eMRO column aliases (e.g. ``hostpartid``,
``onhandnew``); values are strings (the extract coerces Decimal/date to text).

v1 simplifications (documented): vendor economics + lead time collapse to one canonical
vendor per part (``DEFAULT``) so the assembler's vendor resolution always hits; AOG and
repair-TAT stay empty stubs (no extract source); scheduled demand is best-effort.
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
    StockPosition,
    VendorEconomics,
)

from trax_io_reco.data.inventory_state import InMemoryInventoryState

_LOG = logging.getLogger("trax_io.reco.extract_loader")
_CANONICAL_VENDOR = "DEFAULT"
_DEFAULT_TIER = 4

# Default essentiality-code -> canonical 1..5 tier map (tenant-overridable, spec §4.3).
_DEFAULT_ESSENTIALITY_MAP: dict[str, int] = {
    "1": 1, "AOG": 1, "NG": 1, "NOGO": 1, "NO-GO": 1, "NO_GO": 1,
    "2": 2, "GO-IF": 2, "GOIF": 2, "GO_IF": 2,
    "3": 3, "DISPATCH": 3,
    "4": 4, "ROUTINE": 4,
    "5": 5, "CONSUMABLE": 5, "NON-CRITICAL": 5,
}

_REQUIRED_DOMAINS = ("stock_amount", "stock_level_upload", "part_master")

# Real eMRO ``hostparttypeid`` (+ legacy short) codes -> the feature-store's part_class
# Literal. Unknown codes fall back to None rather than a guessed value (design §4.3: hard
# constraints must never be silently fabricated).
_PART_CLASS_MAP: dict[str, str] = {
    "XPENDBL": "expendable", "EXPENDABLE": "expendable", "EXP": "expendable",
    "ROTABLE": "rotable", "ROT": "rotable", "SER": "rotable", "TOOL-SER": "rotable",
    "REPSER": "rotable",
    "REPAIRABLE": "repairable", "REP": "repairable", "NON-SER": "repairable",
    "REP-FA": "repairable",
    "CONSUMABLE": "consumable", "CONS": "consumable", "CON-RAW": "consumable",
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
    return [
        {str(k).lower(): val for k, val in row.items()}
        for row in raw
        if isinstance(row, dict)
    ]


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
    tenant_id = tenant_id or manifest.get("tenant_id") or "tenant"
    extract_date = _parse_date(manifest.get("extract_date")) or date(2026, 4, 1)
    emap = essentiality_map or _DEFAULT_ESSENTIALITY_MAP

    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    rows = {
        d: _load(extract_dir, d)
        for d in (
            "stock_amount", "stock_level_upload", "part_master", "part_criticality",
            "pn_vendor_price", "demand_history_rotables", "demand_history_expendables",
            "location_master", "order_plan", "order_plan_closed_orders",
            "part_chain_details", "events",
        )
    }

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
            tenant_id=tenant_id, pn=pn, location=loc,
            on_hand=serviceable + _i(r.get("onhandbad")) + in_repair,
            serviceable=serviceable, unserviceable_in_repair=in_repair,
            allocated_reserved=_i(r.get("allocated")), rental=_i(r.get("rentalqty")),
            loan=_i(r.get("loanqty")), extract_date=extract_date)
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
        fs.seed(tenant_id, "current_policy", (pn, loc), CurrentPolicy(
            tenant_id=tenant_id, pn=pn, location=loc, rop=_i(r.get("rop")), eoq=_i(r.get("eoq")),
            safety_stock=_i(r.get("safetylevel")), max_stock=_i(r.get("stockmax")),
            replenishment_lead_days=_f(r.get("slreplenishmentlength")), extract_date=extract_date))
        planning_keys.add((pn, loc))

    if pool_by_part:
        # Network pooling (opt-in): sum each PN's stock across ALL physical locations, then
        # assign that PN-network total to every planning key (pn, planning_loc).
        network_totals: dict[str, dict[str, int]] = defaultdict(
            lambda: {"on_hand": 0, "serviceable": 0, "unserviceable_in_repair": 0,
                     "allocated_reserved": 0, "rental": 0, "loan": 0})
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
            fs.seed(tenant_id, "stock_position", (pn, loc), StockPosition(
                tenant_id=tenant_id, pn=pn, location=loc, extract_date=extract_date, **totals))
            keys.add((pn, loc))

    # (c) part_attributes + criticality  <- part_master #15 (+ part_criticality #12)
    for r in rows["part_master"]:
        pn = r.get("hostpartid")
        if not pn:
            continue
        fs.seed(tenant_id, "part_attributes", (pn,), PartAttributes(
            tenant_id=tenant_id, pn=pn, description=r.get("partdescription") or r.get("partname"),
            ata_chapter=_s(r.get("atachapter")), part_class=_part_class(r),
            shelf_life_days=_i(r.get("shelflife")) or None,
            hazardous_material=_truthy(r.get("hazmat")), tool_control_item=_truthy(r.get("tool")),
            fleet_effectivity_tail_count=_i(r.get("nooftails")) or None, extract_date=extract_date))
        raw_code = str(r.get("hostpartcriticalid") or "").strip()
        fs.seed(tenant_id, "criticality", (pn,), Criticality(
            tenant_id=tenant_id, pn=pn, raw_essentiality_code=raw_code or "0",
            canonical_tier=emap.get(raw_code.upper(), _DEFAULT_TIER),  # type: ignore[arg-type]
            extract_date=extract_date))

    # (d) vendor_economics  <- pn_vendor_price #16 (+ part_master costs), collapsed to DEFAULT
    pm_by_pn = {r.get("hostpartid"): r for r in rows["part_master"]}
    seen_vendor: set[str] = set()
    for r in _prefer_rows(rows["pn_vendor_price"]):
        pn = r.get("hostpartid")
        if not pn or pn in seen_vendor:
            continue
        seen_vendor.add(pn)
        pm = pm_by_pn.get(pn, {})
        fs.seed(tenant_id, "vendor_economics", (pn, _CANONICAL_VENDOR), VendorEconomics(
            tenant_id=tenant_id, pn=pn, vendor=_CANONICAL_VENDOR, unit_cost=_dec(r.get("price")),
            market_value_unit_cost=_dec(pm["marketunitcost"]) if pm.get("marketunitcost") else None,
            average_cost=_dec(pm["averagecost"]) if pm.get("averagecost") else None,
            repair_cost_24mo_avg=_dec(pm["repaircost"]) if pm.get("repaircost") else None,
            minimum_order_qty=max(1, _i(r.get("minoq"), 1)), extract_date=extract_date))
        lead = _f(r.get("processinglength"), 21.0) or 21.0
        fs.seed(tenant_id, "lead_time_distribution", (pn, _CANONICAL_VENDOR, "NEW"),
                _lead_time(tenant_id, pn, lead, rows["order_plan_closed_orders"], extract_date))

    # (e) demand_history  <- demand_history_rotables #2 (removals) + _expendables #3 (issues)
    buckets: dict[tuple[str, str], dict[date, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0]))
    for r in rows["demand_history_rotables"]:  # rotable qty is a literal 1 in the SQL
        _bucket(buckets, r, idx=0, qty=_i(r.get("historyamount"), 1) or 1)
    for r in rows["demand_history_expendables"]:  # default 0 -> dropped (no phantom demand)
        _bucket(buckets, r, idx=1, qty=_i(r.get("historyamount")))

    if pool_by_part:
        # Network pooling (opt-in): pool all physical locations' observations into one
        # per-PN series (sum removals/issues per shared bucket; union distinct buckets),
        # then assign the pooled series to every planning key (pn, planning_loc) for that PN.
        pooled_by_pn: dict[str, dict[date, list[int]]] = defaultdict(
            lambda: defaultdict(lambda: [0, 0]))
        for (pn, _loc), months in buckets.items():
            pn_months = pooled_by_pn[pn]
            for m, v in months.items():
                pn_months[m][0] += v[0]
                pn_months[m][1] += v[1]
        for pn, loc in planning_keys:
            months = pooled_by_pn.get(pn, {})
            obs = [DemandObservation(bucket="month", period_start=m, removals=v[0], issues=v[1])
                   for m, v in sorted(months.items())]
            fs.seed(tenant_id, "demand_history", (pn, loc), DemandHistory(
                tenant_id=tenant_id, pn=pn, location=loc, observations=obs,
                extract_date=extract_date))
    else:
        for (pn, loc), months in buckets.items():
            obs = [DemandObservation(bucket="month", period_start=m, removals=v[0], issues=v[1])
                   for m, v in sorted(months.items())]
            fs.seed(tenant_id, "demand_history", (pn, loc), DemandHistory(
                tenant_id=tenant_id, pn=pn, location=loc, observations=obs,
                extract_date=extract_date))
        # Ensure every stock key has a demand_history row (empty -> ultra_rare).
        for pn, loc in keys:
            if (pn, loc) not in buckets:
                fs.seed(tenant_id, "demand_history", (pn, loc), DemandHistory(
                    tenant_id=tenant_id, pn=pn, location=loc, observations=[],
                    extract_date=extract_date))

    # (f) location_graph  <- location_master #5   (optional)
    for r in rows["location_master"]:
        loc = r.get("hostlocid")
        if not loc:
            continue
        main = r.get("hostparentlocid")
        fs.seed(tenant_id, "location_graph", (loc,), LocationGraph(
            tenant_id=tenant_id, location=loc, extract_date=extract_date,
            node=LocationNode(location=loc, related_main_warehouse=main or None,
                              role=("outstation" if main and main != loc else "main"))))

    # (g) open_orders_snapshot  <- order_plan #8 (OPEN)   (optional)
    open_by_key: dict[tuple[str, str], list[OpenOrder]] = defaultdict(list)
    for r in rows["order_plan"]:
        pn, loc = r.get("hostpartid"), r.get("hostlocid")
        if not pn or not loc or str(r.get("orderstatus") or "").upper() != "OPEN":
            continue
        qty_open = max(0, _i(r.get("planquantity")) - _i(r.get("receivedquantity")))
        if qty_open <= 0:
            continue
        otype = "RO" if str(r.get("ordertypeid") or "").upper() == "RO" else "PO"
        open_by_key[(pn, loc)].append(OpenOrder(
            order_id=str(r.get("hostorderid") or "?"), order_type=otype, vendor=None,
            qty_open=qty_open, expected_rcv_date=_parse_date(r.get("planrcvdate"))))
    for (pn, loc), orders in open_by_key.items():
        fs.seed(tenant_id, "open_orders_snapshot", (pn, loc), OpenOrdersSnapshot(
            tenant_id=tenant_id, pn=pn, location=loc, snapshot_at=datetime.combine(
                extract_date, datetime.min.time()),
            orders=orders, total_open_qty=sum(o.qty_open for o in orders),
            extract_date=extract_date))

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


def _prefer_rows(price_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Preferred vendor rows first so the collapsed canonical economics use them.
    return sorted(price_rows, key=lambda r: 0 if _truthy(r.get("preferred")) else 1)


def _bucket(buckets, r, *, idx: int, qty: int) -> None:
    pn, loc = r.get("hostpartid"), r.get("hostlocid")
    month = _month_start(r.get("historybegdate"))
    if not pn or not loc or month is None or qty <= 0:
        return
    buckets[(pn, loc)][month][idx] += qty


def _lead_time(
    tenant_id: str, pn: str, promised: float, closed_rows: list[dict[str, Any]], extract_date: date
) -> LeadTimeDistribution:
    realized = []
    for r in closed_rows:
        if r.get("hostpartid") != pn:
            continue
        ordered, received = _parse_date(r.get("planorderdate")), _parse_date(r.get("actualrcvdate"))
        if ordered and received and received >= ordered:
            realized.append((received - ordered).days)
    realized.sort()
    if realized:
        n = len(realized)
        mean = sum(realized) / n
        p50 = realized[n // 2]
        p90 = realized[min(n - 1, int(round(0.9 * (n - 1))))]
        p99 = realized[min(n - 1, int(round(0.99 * (n - 1))))]
    else:
        mean = p50 = promised
        p90, p99 = promised * 1.3, promised * 1.6
    return LeadTimeDistribution(
        tenant_id=tenant_id, pn=pn, vendor=_CANONICAL_VENDOR, condition="NEW",
        promised_lead_days=promised, realized_mean_days=mean, realized_p50_days=p50,
        realized_p90_days=p90, realized_p99_days=p99,
        promised_vs_actual_delta_mean=(mean - promised), n_observations=len(realized),
        extract_date=extract_date)


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
        fs.seed(tenant_id, "interchangeable_graph", (pn,), InterchangeableGraph(
            tenant_id=tenant_id, pn=pn, group_id=group_id, members=sorted(members),
            edges=edges_by_pn[pn], extract_date=extract_date))
