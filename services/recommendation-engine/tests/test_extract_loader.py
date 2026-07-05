"""Golden test: real-extract-shaped data -> a judge-able recommendation batch.

Proves the bridge (extract_loader) turns a nightly-extract output dir into the same kind of
batch the synthetic demo produces, with no AWS/Oracle/Spark — the shadow-mode dry run.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from trax_io_feature_store import FeatureStoreLookupError, TenantContext

from tests.fixtures.extract_fixture import write_sample_extract
from trax_io_reco.contracts.enums import RecommendationType
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.service import RecommendationService

NOW = datetime(2026, 4, 17, 9, 0, 0)


def _run(extract_dir):
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir)
    batch = RecommendationService(feature_store=fs, inventory_state=inv).run(
        tenant=TenantContext(tenant_id=tenant_id), keys=keys, now=NOW
    )
    return tenant_id, keys, batch


def test_extract_loader_produces_judgeable_batch(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    tenant_id, keys, batch = _run(extract_dir)

    assert tenant_id == "acme"
    assert set(keys) == {
        ("HYD-PUMP-001", "YYZ"), ("HYD-PUMP-001", "YOW"),
        ("FILTER-EXP-042", "YYZ"), ("VALVE-MOD-117", "YYZ"),
    }
    assert batch.skipped == ()  # every seeded key has complete required inputs
    assert batch.recommendations  # a non-empty, judge-able batch
    for r in batch.recommendations:
        assert r.description and r.reason and r.supporting_evidence
        assert 0.0 <= r.confidence_score <= 1.0


def test_extract_loader_transforms_are_correct(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _, _, batch = _run(extract_dir)
    by_part: dict[str, set] = {}
    for r in batch.recommendations:
        by_part.setdefault(r.part_number, set()).add(r.type)

    # Short rotable with an excess sibling at YOW -> transfer beats purchase.
    yyz_pump = {r.type for r in batch.recommendations
                if r.part_number == "HYD-PUMP-001" and r.current_location == "YYZ"}
    assert RecommendationType.TRANSFER in yyz_pump
    assert RecommendationType.PURCHASE not in yyz_pump

    # High-value (>$5k), zero-usage, deep-overstock expendable -> sell.
    assert RecommendationType.SELL in by_part.get("FILTER-EXP-042", set())

    # Busy part with a far-too-low current Max -> adjust min/max.
    assert RecommendationType.ADJUST_MIN_MAX in by_part.get("VALVE-MOD-117", set())


def test_extract_loader_is_deterministic(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _, _, b1 = _run(extract_dir)
    _, _, b2 = _run(extract_dir)
    strip = lambda b: [  # noqa: E731
        r.model_copy(update={"recommendation_id": "X"}).model_dump_json()
        for r in b.recommendations
    ]
    assert strip(b1) == strip(b2)


# --- review-fix regressions: edge-case robustness on messy real extract data --- #
def _write(extract_dir, domain, rows):
    (extract_dir / f"{domain}.json").write_text(json.dumps(rows))


def test_survives_nonfinite_values(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    # A single 'Infinity' qty and a 'NaN' price must NOT crash the load.
    _write(extract_dir, "stock_amount", [
        {"hostpartid": "BAD-1", "hostlocid": "YYZ", "onhandnew": "Infinity", "onhandbad": "0",
         "inrepair": "0", "allocated": "0", "rentalqty": "0", "loanqty": "0"},
    ])
    _write(extract_dir, "pn_vendor_price", [
        {"hostvendorlocid": "V", "hostpartid": "BAD-1", "price": "NaN", "processinglength": "21",
         "condition": "NEW", "preferred": "Y", "minoq": "1"},
    ])
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir)  # no exception
    assert ("BAD-1", "YYZ") in keys


def test_missing_required_domain_raises(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    (extract_dir / "stock_amount.json").unlink()  # a failed/partial extract
    with pytest.raises(FileNotFoundError):
        build_stores_from_extract(extract_dir)


def test_tolerates_corrupt_optional_domain(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    (extract_dir / "location_master.json").write_text("{ this is not valid json ]")
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir)  # no exception
    assert keys  # the run still produces a population


def test_parses_oracle_mmddyyyy_dates(tmp_path) -> None:
    # Oracle-native date format must NOT silently drop demand (the subtle one).
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(extract_dir, "demand_history_expendables", [
        {"hostpartid": "VALVE-MOD-117", "hostlocid": "YYZ",
         "historybegdate": f"0{m}/15/2025 08:30", "historyamount": "30",
         "transactiontype": "ISSUED"}
        for m in range(1, 10)
    ])
    fs, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    dh = fs.get_demand_history(tenant=TenantContext(tenant_id=tenant_id),
                               pn="VALVE-MOD-117", location="YYZ")
    assert dh.observations  # demand was parsed, not dropped
    assert sum(o.issues for o in dh.observations) == 270


# --- R1: opt-in network pooling (planning vs. physical stocking locations) --- #
_PN = "PUMP-NET-001"
_PLANNING_LOC = "LP"
_PHYS_1 = "L1"
_PHYS_2 = "L2"


def _write_pooling_fixture(tmp_path):
    """One PN with policy at a planning location LP and stock/demand rows at two distinct
    physical stocking locations L1 (on_hand 4) and L2 (on_hand 6)."""
    extract_dir = write_sample_extract(tmp_path / "extract_pool")
    _write(extract_dir, "stock_amount", [
        {"hostpartid": _PN, "hostlocid": _PHYS_1, "onhandnew": "4", "onhandbad": "0",
         "inrepair": "0", "allocated": "1", "rentalqty": "0", "loanqty": "0"},
        {"hostpartid": _PN, "hostlocid": _PHYS_2, "onhandnew": "6", "onhandbad": "0",
         "inrepair": "0", "allocated": "2", "rentalqty": "1", "loanqty": "0"},
    ])
    _write(extract_dir, "stock_level_upload", [
        {"hostpartid": _PN, "hostlocid": _PLANNING_LOC, "rop": "5", "eoq": "5",
         "safetylevel": "2", "stockmax": "20", "slreplenishmentlength": "21"},
    ])
    _write(extract_dir, "part_master", [
        {"hostpartid": _PN, "partdescription": "NETWORK PUMP", "atachapter": "29",
         "hostpartcriticalid": "4", "shelflife": "0", "hazmat": "N", "tool": "N",
         "nooftails": "10", "partrepairable": "Y", "partserializable": "Y", "ispartkit": "N",
         "marketunitcost": "0", "averagecost": "0", "repaircost": "0"},
    ])
    _write(extract_dir, "demand_history_rotables", [
        {"hostpartid": _PN, "hostlocid": _PHYS_1, "historybegdate": "2025-01-15",
         "historyamount": "1", "transactiontype": "REMOVE"},
        {"hostpartid": _PN, "hostlocid": _PHYS_2, "historybegdate": "2025-01-15",
         "historyamount": "1", "transactiontype": "REMOVE"},
        {"hostpartid": _PN, "hostlocid": _PHYS_2, "historybegdate": "2025-02-15",
         "historyamount": "1", "transactiontype": "REMOVE"},
    ])
    _write(extract_dir, "demand_history_expendables", [])
    _write(extract_dir, "pn_vendor_price", [])
    return extract_dir


def test_pool_by_part_pools_stock_and_demand_across_physical_locations(tmp_path) -> None:
    extract_dir = _write_pooling_fixture(tmp_path)
    fs, _, tenant_id, keys = build_stores_from_extract(extract_dir, pool_by_part=True)

    assert (_PN, _PLANNING_LOC) in keys
    # Physical-location keys must NOT appear as planning keys under pooling.
    assert (_PN, _PHYS_1) not in keys
    assert (_PN, _PHYS_2) not in keys

    pos = fs.get_stock_position(tenant=TenantContext(tenant_id=tenant_id),
                                 pn=_PN, location=_PLANNING_LOC)
    assert pos.on_hand == 10  # network sum: 4 + 6
    assert pos.serviceable == 10
    assert pos.allocated_reserved == 3  # 1 + 2
    assert pos.rental == 1  # 0 + 1

    dh = fs.get_demand_history(tenant=TenantContext(tenant_id=tenant_id),
                                pn=_PN, location=_PLANNING_LOC)
    # Two rows share the 2025-01-15 bucket (pooled: 1 + 1 = 2); one more at 2025-02-15.
    total_removals = sum(o.removals for o in dh.observations)
    assert total_removals == 3
    buckets_by_period = {o.period_start: o.removals for o in dh.observations}
    assert buckets_by_period[date(2025, 1, 1)] == 2
    assert buckets_by_period[date(2025, 2, 1)] == 1


# --- R3: real-eMRO type coercion (Oracle returns int/None where the sample used strings) --- #
def test_real_emro_types_are_coerced(tmp_path) -> None:
    """A real eMRO extract returns atachapter as int, hazmat/tool as 'Y'/'N' strings, and
    hostparttypeid as an eMRO type code (e.g. 'XPENDBL') -- none of which is how the clean
    sample extract shapes them. The loader must coerce these into what PartAttributes expects
    instead of raising a pydantic ValidationError."""
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(extract_dir, "part_master", [
        {"hostpartid": "REAL-1", "partdescription": "REAL PART", "atachapter": 0,
         "hostpartcriticalid": "4", "shelflife": 0, "hazmat": "Y", "tool": "N",
         "nooftails": 10, "hostparttypeid": "XPENDBL"},
    ])
    fs, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    attrs = fs.get_part_attributes(tenant=TenantContext(tenant_id=tenant_id), pn="REAL-1")
    assert attrs.ata_chapter == "0"
    assert attrs.hazardous_material is True
    assert attrs.tool_control_item is False
    assert attrs.part_class == "expendable"


def test_pool_by_part_default_off_is_unchanged(tmp_path) -> None:
    extract_dir = _write_pooling_fixture(tmp_path)
    fs, _, tenant_id, keys = build_stores_from_extract(extract_dir)  # pool_by_part defaults off

    # Legacy behavior: keys are per physical stocking location, not the planning location.
    assert (_PN, _PHYS_1) in keys
    assert (_PN, _PHYS_2) in keys
    assert (_PN, _PLANNING_LOC) not in keys

    pos1 = fs.get_stock_position(tenant=TenantContext(tenant_id=tenant_id),
                                  pn=_PN, location=_PHYS_1)
    pos2 = fs.get_stock_position(tenant=TenantContext(tenant_id=tenant_id),
                                  pn=_PN, location=_PHYS_2)
    assert pos1.on_hand == 4
    assert pos2.on_hand == 6


def test_pool_by_part_drops_zero_policy_planning_rows(tmp_path) -> None:
    # W3-5 planning-active guard (defense in depth behind the extract-side predicate):
    # a network-wide extract can carry every location row of a scoped part (rop=0 and
    # stockmax=0 at most of them). Pooled runs must not turn those into planning keys —
    # on the real DB the universe exploded to 984,021 keys vs the true 62,492.
    extract_dir = _write_pooling_fixture(tmp_path)
    _write(extract_dir, "stock_level_upload", [
        {"hostpartid": _PN, "hostlocid": _PLANNING_LOC, "rop": "5", "eoq": "5",
         "safetylevel": "2", "stockmax": "20", "slreplenishmentlength": "21"},
        {"hostpartid": _PN, "hostlocid": "LZERO", "rop": "0", "eoq": "0",
         "safetylevel": "0", "stockmax": "0", "slreplenishmentlength": "0"},
    ])
    fs, _, tenant_id, keys = build_stores_from_extract(extract_dir, pool_by_part=True)

    assert (_PN, _PLANNING_LOC) in keys
    assert (_PN, "LZERO") not in keys
    with pytest.raises(FeatureStoreLookupError):
        fs.get_current_policy(
            tenant=TenantContext(tenant_id=tenant_id), pn=_PN, location="LZERO"
        )


def test_unpooled_keeps_zero_policy_rows(tmp_path) -> None:
    # The unpooled (sample/legacy) path stays byte-identical: zero-policy rows still
    # seed current_policy exactly as before the pooled guard existed.
    extract_dir = _write_pooling_fixture(tmp_path)
    _write(extract_dir, "stock_level_upload", [
        {"hostpartid": _PN, "hostlocid": "LZERO", "rop": "0", "eoq": "0",
         "safetylevel": "0", "stockmax": "0", "slreplenishmentlength": "0"},
    ])
    fs, _, tenant_id, _keys = build_stores_from_extract(extract_dir)
    pol = fs.get_current_policy(
        tenant=TenantContext(tenant_id=tenant_id), pn=_PN, location="LZERO"
    )
    assert pol.rop == 0
    assert pol.max_stock == 0


def test_extract_loader_wires_requisition_snapshot(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(extract_dir, "order_plan_data_requisition", [
        {"hostpartid": "HYD-PUMP-001", "hostlocid": "YYZ", "hostorderid": "REQ_1001_1",
         "orderstatus": "OPEN", "planquantity": "5", "receivedquantity": "2",
         "planrcvdate": "2026-05-01", "hostreplsourcelocid": "YOW"},
        {"hostpartid": "HYD-PUMP-001", "hostlocid": "YYZ", "hostorderid": "REQ_1002_1",
         "orderstatus": "CLOSED", "planquantity": "1", "receivedquantity": "0",
         "planrcvdate": "2026-05-02", "hostreplsourcelocid": None},
    ])
    fs, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    snap = fs.get_requisition_snapshot(
        tenant=TenantContext(tenant_id=tenant_id), pn="HYD-PUMP-001", location="YYZ"
    )
    assert snap.total_qty_needed == 3  # only the OPEN line counts: 5 - 2 = 3
    assert len(snap.lines) == 1
    assert snap.lines[0].requisition_id == "REQ_1001_1"
    assert snap.lines[0].need_by == date(2026, 5, 1)
    assert snap.lines[0].alt_source_location == "YOW"
