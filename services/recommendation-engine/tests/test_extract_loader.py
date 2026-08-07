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
from trax_io_reco.contracts.enums import EvidenceKind, RecommendationType
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.position.repair_pipeline import build_repair_pipeline
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


@pytest.mark.parametrize(
    ("domain", "payload"),
    [
        ("stock_amount", "{ this is not valid json ]"),
        ("stock_amount", "{}"),
        ("stock_amount", '[{"hostpartid": "P-1"}, null]'),
        ("stock_level_upload", "{ this is not valid json ]"),
        ("stock_level_upload", "{}"),
        ("stock_level_upload", '[{"hostpartid": "P-1"}, null]'),
        ("part_master", "{ this is not valid json ]"),
        ("part_master", "{}"),
        ("part_master", '[{"hostpartid": "P-1"}, null]'),
    ],
)
def test_required_domain_rejects_malformed_artifact(
    tmp_path,
    domain: str,
    payload: str,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    (extract_dir / f"{domain}.json").write_text(payload)

    with pytest.raises(ValueError, match=domain):
        build_stores_from_extract(extract_dir)


@pytest.mark.parametrize("domain", ["stock_amount", "stock_level_upload", "part_master"])
def test_failed_required_domain_metadata_overrides_stale_file(
    tmp_path,
    domain: str,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    manifest_path = extract_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for artifact in manifest["artifacts"]:
        if artifact["domain"] == domain:
            artifact["status"] = "failed"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match=domain):
        build_stores_from_extract(extract_dir)


def test_tolerates_corrupt_optional_domain(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    (extract_dir / "location_master.json").write_text("{ this is not valid json ]")
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir)  # no exception
    assert keys  # the run still produces a population


@pytest.mark.parametrize(
    ("domain", "payload"),
    [
        ("order_plan", "{ this is not valid json ]"),
        ("order_plan", "{}"),
        ("order_plan_data_requisition", "{ this is not valid json ]"),
        ("order_plan_data_requisition", "{}"),
    ],
)
def test_successful_availability_feed_rejects_malformed_artifact(
    tmp_path,
    domain: str,
    payload: str,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    (extract_dir / f"{domain}.json").write_text(payload)

    with pytest.raises(ValueError, match=domain):
        build_stores_from_extract(extract_dir)


@pytest.mark.parametrize(
    ("domain", "payload"),
    [
        ("demand_history_rotables", "{ this is not valid json ]"),
        ("demand_history_rotables", "{}"),
        ("demand_history_rotables", '[{"hostpartid": "P-1"}, null]'),
        ("demand_history_expendables", "{ this is not valid json ]"),
        ("demand_history_expendables", "{}"),
        ("demand_history_expendables", '[{"hostpartid": "P-1"}, null]'),
    ],
)
def test_successful_demand_domain_rejects_malformed_artifact(
    tmp_path,
    domain: str,
    payload: str,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    (extract_dir / f"{domain}.json").write_text(payload)

    with pytest.raises(ValueError, match=domain):
        build_stores_from_extract(extract_dir)


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


def test_extract_loader_preserves_open_repair_identity_age_status_and_shop(
    tmp_path,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(
        extract_dir,
        "order_plan",
        [
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "orderstatus": "IN_PROGRESS",
                "ordertypeid": "RO",
                "hostorderid": "RO-101",
                "orderlineid": "1",
                "planquantity": "1",
                "receivedquantity": "0",
                "planrcvdate": "",
                "planorderdate": "2026-04-02T13:15:00Z",
                "hostvendorlocid": "VENDOR-1",
                "hostshopid": "SHOP-1",
                "serialnumber": "SER-1",
            },
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "orderstatus": "AWAITING_PARTS",
                "ordertypeid": "RO",
                "hostorderid": "RO-102",
                "orderlineid": None,
                "planquantity": "2",
                "receivedquantity": "0",
                "planrcvdate": "",
                "planorderdate": None,
                "hostvendorlocid": "VENDOR-2",
                "hostshopid": "SHOP-2",
                "serialnumber": None,
            },
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "orderstatus": "IN_PROGRESS",
                "ordertypeid": "PO",
                "hostorderid": "PO-NOT-OPEN",
                "orderlineid": "1",
                "planquantity": "9",
                "receivedquantity": "0",
                "planrcvdate": "2026-05-01",
                "planorderdate": "2026-04-01",
                "hostvendorlocid": "VENDOR-3",
            },
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "orderstatus": "OPEN",
                "ordertypeid": "PO",
                "hostorderid": "PO-OPEN",
                "orderlineid": "3",
                "planquantity": "4",
                "receivedquantity": "0",
                "planrcvdate": "2026-05-02",
                "planorderdate": "2026-04-03",
                "hostvendorlocid": "VENDOR-4",
            },
        ],
    )

    feature_store, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    snapshot = feature_store.get_open_orders_snapshot(
        tenant=TenantContext(tenant_id=tenant_id),
        pn="HYD-PUMP-001",
        location="YYZ",
    )

    assert snapshot.total_open_qty == 7
    by_id = {order.order_id: order for order in snapshot.orders}
    assert set(by_id) == {"RO-101", "RO-102", "PO-OPEN"}
    assert by_id["RO-101"].model_dump() == {
        "order_id": "RO-101",
        "order_type": "RO",
        "vendor": "VENDOR-1",
        "qty_open": 1,
        "expected_rcv_date": None,
        "order_line_id": "1",
        "opened_at": by_id["RO-101"].opened_at,
        "status": "IN_PROGRESS",
        "serial_number": "SER-1",
        "shop": "SHOP-1",
        "location": "YYZ",
    }
    assert by_id["RO-101"].opened_at == datetime.fromisoformat(
        "2026-04-02T13:15:00+00:00"
    )
    assert by_id["RO-102"].order_line_id is None
    assert by_id["RO-102"].opened_at is None
    assert by_id["RO-102"].status == "AWAITING_PARTS"
    assert by_id["PO-OPEN"].status == "OPEN"

    repair_pipeline = build_repair_pipeline(
        tenant_id=tenant_id,
        part_number="HYD-PUMP-001",
        location_code="YYZ",
        open_orders=snapshot,
        aggregate_wip_quantity=3,
        as_of=date(2026, 4, 17),
    )
    assert len(repair_pipeline.included) == 1
    included = repair_pipeline.included[0]
    assert included.age_days == 15
    assert included.work_item.model_dump() == {
        "contract_version": "repair-work-item.v1",
        "tenant_id": tenant_id,
        "repair_order_id": "RO-101",
        "repair_line_id": "1",
        "part_number": "HYD-PUMP-001",
        "quantity": 1,
        "location_code": "YYZ",
        "opened_at": datetime.fromisoformat("2026-04-02T13:15:00+00:00"),
        "status": "in_progress",
        "shop_code": "SHOP-1",
        "vendor_code": "VENDOR-1",
        "serial_number": "SER-1",
    }
    assert any(
        exclusion.repair_order_id == "RO-102"
        and exclusion.reason == "missing_line_identity"
        and exclusion.quantity == 2
        for exclusion in repair_pipeline.exclusions
    )
    assert repair_pipeline.time_phased_credit_quantity == 0


def test_open_order_classification_never_defaults_unknown_rows_to_purchase(
    tmp_path,
    caplog,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(
        extract_dir,
        "order_plan",
        [
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "orderstatus": "OPEN",
                "hostorderid": "PO-LEGACY",
                "planquantity": "3",
                "receivedquantity": "0",
                "planrcvdate": "2026-05-02",
            },
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "orderstatus": "IN_PROGRESS",
                "hostorderid": "RO/LEGACY",
                "planquantity": "2",
                "receivedquantity": "0",
                "planrcvdate": "",
                "planorderdate": "2026-04-01",
            },
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "orderstatus": "OPEN",
                "hostorderid": "NO-SAFE-PREFIX",
                "planquantity": "11",
                "receivedquantity": "0",
                "planrcvdate": "2026-05-02",
            },
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "orderstatus": "OPEN",
                "ordertypeid": "UNKNOWN",
                "hostorderid": "PO-EXPLICIT-CONFLICT",
                "planquantity": "13",
                "receivedquantity": "0",
                "planrcvdate": "2026-05-02",
            },
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "orderstatus": "OPEN",
                "hostorderid": "PO-CONFLICT",
                "orderid": "RO-CONFLICT",
                "planquantity": "17",
                "receivedquantity": "0",
                "planrcvdate": "2026-05-02",
            },
        ],
    )

    feature_store, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    snapshot = feature_store.get_open_orders_snapshot(
        tenant=TenantContext(tenant_id=tenant_id),
        pn="HYD-PUMP-001",
        location="YYZ",
    )

    assert {
        (order.order_id, order.order_type, order.qty_open)
        for order in snapshot.orders
    } == {
        ("PO-LEGACY", "PO", 3),
        ("RO/LEGACY", "RO", 2),
    }
    assert snapshot.total_open_qty == 5
    assert "excluded 3 open-order row(s) with unclassified order type" in caplog.text


def test_terminal_repair_order_remains_available_as_exclusion_evidence(
    tmp_path,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(
        extract_dir,
        "order_plan",
        [
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "orderstatus": "CLOSED",
                "ordertypeid": "RO",
                "hostorderid": "RO-CLOSED",
                "orderlineid": "1",
                "planquantity": "1",
                "receivedquantity": "0",
                "planrcvdate": "",
                "planorderdate": "2026-04-01",
            },
        ],
    )

    feature_store, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    snapshot = feature_store.get_open_orders_snapshot(
        tenant=TenantContext(tenant_id=tenant_id),
        pn="HYD-PUMP-001",
        location="YYZ",
    )
    pipeline = build_repair_pipeline(
        tenant_id=tenant_id,
        part_number="HYD-PUMP-001",
        location_code="YYZ",
        open_orders=snapshot,
        aggregate_wip_quantity=1,
        as_of=date(2026, 4, 17),
    )

    assert [order.order_id for order in snapshot.orders] == ["RO-CLOSED"]
    assert pipeline.aggregate_residual_quantity == 0
    assert pipeline.exclusions[0].reason == "terminal_status"
    assert pipeline.exclusions[0].quantity == 1


def test_extract_loader_persists_configured_window_and_event_counts(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    manifest_path = extract_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for artifact in manifest["artifacts"]:
        if artifact["domain"] in {
            "demand_history_rotables",
            "demand_history_expendables",
        }:
            artifact["bind_vars"] = {
                "from_date": "2023-04-16",
                "to_date": "2026-04-16",
            }
    manifest_path.write_text(json.dumps(manifest))
    _write(
        extract_dir,
        "demand_history_expendables",
        [
            {
                "hostpartid": "VALVE-MOD-117",
                "hostlocid": "YYZ",
                "historybegdate": "2026-04-16",
                "historyamount": "7",
                "transactiontype": "ISSUED",
            }
        ],
    )

    fs, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    history = fs.get_demand_history(
        tenant=TenantContext(tenant_id=tenant_id),
        pn="VALVE-MOD-117",
        location="YYZ",
    )

    assert history.observation_start == date(2023, 4, 16)
    assert history.observation_end == date(2026, 4, 16)
    assert history.event_count_source == "observed"
    assert history.bucket == "month"
    assert sum(o.issues for o in history.observations) == 7
    assert sum(o.issue_events or 0 for o in history.observations) == 1


def test_configured_zero_demand_stock_key_gets_zero_marker(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(extract_dir, "demand_history_rotables", [])
    _write(extract_dir, "demand_history_expendables", [])

    fs, _, tenant_id, keys = build_stores_from_extract(extract_dir)
    pn, location = next(iter(keys))
    history = fs.get_demand_history(
        tenant=TenantContext(tenant_id=tenant_id),
        pn=pn,
        location=location,
    )

    assert history.observation_start == date(2023, 4, 1)
    assert history.observation_end == date(2026, 4, 1)
    assert len(history.observations) == 1
    marker = history.observations[0]
    assert marker.removals == marker.issues == 0
    assert marker.removal_events == marker.issue_events == 0


@pytest.mark.parametrize(
    "failed_domain",
    ["demand_history_rotables", "demand_history_expendables"],
)
def test_partial_demand_feed_marks_every_planning_key_unavailable(
    tmp_path,
    failed_domain: str,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    manifest_path = extract_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for artifact in manifest["artifacts"]:
        if artifact["domain"] == failed_domain:
            artifact["status"] = "failed"
    manifest_path.write_text(json.dumps(manifest))

    fs, inventory_state, tenant_id, keys = build_stores_from_extract(extract_dir)
    tenant = TenantContext(tenant_id=tenant_id)

    for pn, location in keys:
        history = fs.get_demand_history(
            tenant=tenant,
            pn=pn,
            location=location,
        )
        assert history.observation_start is None
        assert history.observation_end is None
        assert history.event_count_source == "unavailable"
        assert history.observations == []

    batch = RecommendationService(
        feature_store=fs,
        inventory_state=inventory_state,
    ).run(tenant=tenant, keys=keys, now=NOW)
    assert batch.recommendations == ()
    assert {skipped.reason for skipped in batch.skipped} == {
        "demand_history_unavailable"
    }


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


def test_successful_empty_optional_feeds_are_available_for_every_planning_key(
    tmp_path,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")

    fs, inventory_state, tenant_id, keys = build_stores_from_extract(extract_dir)
    tenant = TenantContext(tenant_id=tenant_id)

    for pn, location in keys:
        open_orders = fs.get_open_orders_snapshot(
            tenant=tenant,
            pn=pn,
            location=location,
        )
        requisitions = fs.get_requisition_snapshot(
            tenant=tenant,
            pn=pn,
            location=location,
        )
        assert open_orders.orders == []
        assert open_orders.total_open_qty == 0
        assert requisitions.lines == []
        assert requisitions.total_qty_needed == 0
        assert (
            inventory_state.get_scheduled_demand(
                tenant=tenant,
                pn=pn,
                location=location,
            )
            == ()
        )
        assert (
            inventory_state.get_scheduled_demand_status(
                tenant=tenant,
                pn=pn,
                location=location,
            )
            == "available"
        )


def test_failed_optional_feed_metadata_overrides_stale_files(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(
        extract_dir,
        "order_plan",
        [
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "hostorderid": "STALE-PO",
                "orderstatus": "OPEN",
                "planquantity": "8",
                "receivedquantity": "0",
            }
        ],
    )
    _write(
        extract_dir,
        "order_plan_data_requisition",
        [
            {
                "hostpartid": "HYD-PUMP-001",
                "hostlocid": "YYZ",
                "hostorderid": "STALE-REQ",
                "orderstatus": "OPEN",
                "planquantity": "5",
                "receivedquantity": "0",
                "planrcvdate": "2026-05-01",
            }
        ],
    )
    manifest_path = extract_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for artifact in manifest["artifacts"]:
        if artifact["domain"] in {"order_plan", "order_plan_data_requisition"}:
            artifact["status"] = "failed"
    manifest_path.write_text(json.dumps(manifest))

    fs, inventory_state, tenant_id, _keys = build_stores_from_extract(extract_dir)
    tenant = TenantContext(tenant_id=tenant_id)

    with pytest.raises(FeatureStoreLookupError):
        fs.get_open_orders_snapshot(
            tenant=tenant,
            pn="HYD-PUMP-001",
            location="YYZ",
        )
    with pytest.raises(FeatureStoreLookupError):
        fs.get_requisition_snapshot(
            tenant=tenant,
            pn="HYD-PUMP-001",
            location="YYZ",
        )
    assert (
        inventory_state.get_scheduled_demand(
            tenant=tenant,
            pn="HYD-PUMP-001",
            location="YYZ",
        )
        == ()
    )
    assert (
        inventory_state.get_scheduled_demand_status(
            tenant=tenant,
            pn="HYD-PUMP-001",
            location="YYZ",
        )
        == "unavailable"
    )


def test_legacy_manifest_uses_optional_file_presence_as_availability(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    manifest_path = extract_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"] = [
        artifact
        for artifact in manifest["artifacts"]
        if artifact["domain"] not in {"order_plan", "order_plan_data_requisition"}
    ]
    manifest_path.write_text(json.dumps(manifest))

    fs, inventory_state, tenant_id, keys = build_stores_from_extract(extract_dir)
    tenant = TenantContext(tenant_id=tenant_id)
    pn, location = keys[0]

    assert fs.get_open_orders_snapshot(
        tenant=tenant,
        pn=pn,
        location=location,
    ).orders == []
    assert fs.get_requisition_snapshot(
        tenant=tenant,
        pn=pn,
        location=location,
    ).lines == []
    assert (
        inventory_state.get_scheduled_demand_status(
            tenant=tenant,
            pn=pn,
            location=location,
        )
        == "available"
    )


def test_extract_loader_requisition_reaches_part_location_context(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(extract_dir, "order_plan_data_requisition", [
        {"hostpartid": "HYD-PUMP-001", "hostlocid": "YYZ", "hostorderid": "REQ_2001_1",
         "orderstatus": "OPEN", "planquantity": "4", "receivedquantity": "0",
         "planrcvdate": "2026-06-01", "hostreplsourcelocid": None},
    ])
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir)
    assembler = ContextAssembler(features=FeatureReader(fs), inventory_state=inv)
    ctx = assembler.assemble(
        tenant=TenantContext(tenant_id=tenant_id), pn="HYD-PUMP-001", location="YYZ"
    )
    assert ctx.requisition is not None
    assert ctx.requisition.total_qty_needed == 4

    # The successful feed makes a rowless key observed-empty, not unavailable.
    ctx2 = assembler.assemble(
        tenant=TenantContext(tenant_id=tenant_id), pn="FILTER-EXP-042", location="YYZ"
    )
    assert ctx2.requisition is not None
    assert ctx2.requisition.lines == []
    assert ctx2.scheduled_demand == ()
    assert ctx2.scheduled_demand_status == "available"


def test_dated_open_requisition_becomes_boundary_scheduled_demand(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(extract_dir, "order_plan_data_requisition", [
        {
            "hostpartid": "FILTER-EXP-042",
            "hostlocid": "YYZ",
            "hostorderid": "REQ-BOUNDARY",
            "orderstatus": "OPEN",
            "planquantity": "7",
            "receivedquantity": "0",
            "planrcvdate": "2026-05-17",  # inclusive 30-day boundary from NOW
            "hostreplsourcelocid": None,
        },
        {
            "hostpartid": "FILTER-EXP-042",
            "hostlocid": "YYZ",
            "hostorderid": "REQ-UNDATED",
            "orderstatus": "OPEN",
            "planquantity": "99",
            "receivedquantity": "0",
            "planrcvdate": None,
            "hostreplsourcelocid": None,
        },
    ])

    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir)
    tenant = TenantContext(tenant_id=tenant_id)
    scheduled = inv.get_scheduled_demand(
        tenant=tenant,
        pn="FILTER-EXP-042",
        location="YYZ",
    )

    assert len(scheduled) == 1
    assert scheduled[0].due_date == date(2026, 5, 17)
    assert scheduled[0].qty == 7
    assert scheduled[0].source_ref == "REQ-BOUNDARY"
    assert scheduled[0].source_kind == EvidenceKind.REQUISITION

    batch = RecommendationService(feature_store=fs, inventory_state=inv).run(
        tenant=tenant,
        keys=keys,
        now=NOW,
        reporting_horizon_days=30,
    )
    # The dated boundary item remains observable, but the undated open line makes
    # future-demand coverage partial.  Partial coverage must not be treated as
    # evidence that the remaining inventory is disposable.
    assert not any(
        recommendation.part_number == "FILTER-EXP-042"
        and recommendation.type
        in {RecommendationType.REDUCE_STOCK, RecommendationType.SELL}
        for recommendation in batch.recommendations
    )
