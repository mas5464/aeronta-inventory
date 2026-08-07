"""Mapper: canonical parsed rows -> the engine's eMRO-native extract dir shape. The real
gate is that the mapped output loads through the actual engine loader unchanged."""
import json
from datetime import date, datetime

import pytest
from trax_io_feature_store import FeatureStoreLookupError, TenantContext

from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.demand.basis import historical_demand_stats, scheduled_units_in_horizon
from trax_io_reco.ingest.mapper import to_extract_dir
from trax_io_reco.service import RecommendationService


def test_mapper_produces_loadable_extract(tmp_path):
    parsed = {
        "parts": [{"part_number": "P1", "part_class": "rotable", "unit_cost": "100",
                   "criticality": "AOG"}],
        "stock": [{"part_number": "P1", "location_code": "MIA", "on_hand": "5",
                   "current_rop": "3", "current_eoq": "10", "current_safety_stock": "2",
                   "current_max": "20"}],
        "demand_history": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "period": "2026-01-01",
                "quantity": "3",
                "observation_start": "2025-11-01",
                "observation_end": "2026-03-31",
            }
        ],
    }
    out = tmp_path / "extract"
    out.mkdir()
    to_extract_dir(parsed, out, tenant_id="t1")
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["extract_date"] == "2026-03-31"
    assert manifest["extract_date_source"] == "demand_observation_end"
    # required domain files exist with the mapped eMRO keys
    pm = json.loads((out / "part_master.json").read_text())
    assert pm[0]["hostpartid"] == "P1" and pm[0]["marketunitcost"] == "100"
    sa = json.loads((out / "stock_amount.json").read_text())
    assert sa[0]["onhandnew"] == "5" and sa[0]["hostlocid"] == "MIA"
    slu = json.loads((out / "stock_level_upload.json").read_text())
    assert slu[0]["rop"] == "3"
    # rotable demand routed to the rotables file
    assert (out / "demand_history_rotables.json").exists()
    # and the whole thing loads through the real engine loader
    fs, inv, tid, keys = build_stores_from_extract(str(out), tenant_id="t1")
    assert ("P1", "MIA") in keys


def test_mapper_persists_upload_window_and_one_event_per_quantity_row(tmp_path) -> None:
    parsed = {
        "parts": [
            {
                "part_number": "P1",
                "part_class": "expendable",
                "unit_cost": "100",
                "criticality": "AOG",
            }
        ],
        "stock": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "on_hand": "5",
                "current_rop": "3",
                "current_eoq": "10",
                "current_safety_stock": "2",
                "current_max": "20",
            }
        ],
        "demand_history": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "period": "2026-01-01",
                "quantity": "7",
                "observation_start": "2023-04-16",
                "observation_end": "2026-04-16",
            }
        ],
    }
    out = tmp_path / "extract"
    to_extract_dir(parsed, out, tenant_id="t1")

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["extract_date"] == "2026-04-16"
    assert manifest["extract_date_source"] == "demand_observation_end"
    demand_artifacts = {
        artifact["domain"]: artifact for artifact in manifest["artifacts"]
    }
    expected_binds = {
        "from_date": "2023-04-16",
        "to_date": "2026-04-16",
    }
    assert demand_artifacts["demand_history_rotables"]["bind_vars"] == expected_binds
    assert demand_artifacts["demand_history_expendables"]["bind_vars"] == expected_binds

    feature_store, _, _, _ = build_stores_from_extract(out, tenant_id="t1")
    history = feature_store.get_demand_history(
        tenant=TenantContext(tenant_id="t1"),
        pn="P1",
        location="MIA",
    )
    assert history.observation_start.isoformat() == "2023-04-16"
    assert history.observation_end.isoformat() == "2026-04-16"
    assert sum(observation.issues for observation in history.observations) == 7
    assert sum(observation.issue_events or 0 for observation in history.observations) == 1


def test_mapper_is_repeatable_and_accepts_explicit_snapshot_as_of(tmp_path) -> None:
    parsed = {
        "parts": [{"part_number": "P1", "part_class": "rotable"}],
        "stock": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "on_hand": "5",
            }
        ],
        "demand_history": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "period": "2024-01-01",
                "quantity": "1",
                "observation_start": "2023-01-01",
                "observation_end": "2024-03-31",
            }
        ],
    }
    first = tmp_path / "first"
    second = tmp_path / "second"
    to_extract_dir(parsed, first, tenant_id="t1")
    to_extract_dir(parsed, second, tenant_id="t1")

    assert (first / "manifest.json").read_bytes() == (
        second / "manifest.json"
    ).read_bytes()

    explicit = tmp_path / "explicit"
    to_extract_dir(
        parsed,
        explicit,
        tenant_id="t1",
        snapshot_as_of=date(2026, 4, 16),
    )
    manifest = json.loads((explicit / "manifest.json").read_text())
    assert manifest["extract_date"] == "2026-04-16"
    assert manifest["extract_date_source"] == "explicit"


def test_mapper_records_only_optional_source_domains_actually_supplied(tmp_path) -> None:
    base = {
        "parts": [{"part_number": "P1"}],
        "stock": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "on_hand": "5",
            }
        ],
    }
    with_open_orders = tmp_path / "with-open-orders"
    to_extract_dir(
        {**base, "open_orders": []},
        with_open_orders,
        tenant_id="t1",
    )
    supplied_manifest = json.loads(
        (with_open_orders / "manifest.json").read_text()
    )
    assert {
        artifact["domain"] for artifact in supplied_manifest["artifacts"]
    } == {"order_plan"}
    assert (with_open_orders / "order_plan.json").exists()
    assert not (with_open_orders / "order_plan_data_requisition.json").exists()
    feature_store, inventory_state, _, keys = build_stores_from_extract(
        with_open_orders,
        tenant_id="t1",
    )
    tenant = TenantContext(tenant_id="t1")
    pn, location = keys[0]
    assert feature_store.get_open_orders_snapshot(
        tenant=tenant,
        pn=pn,
        location=location,
    ).orders == []
    with pytest.raises(FeatureStoreLookupError):
        feature_store.get_requisition_snapshot(
            tenant=tenant,
            pn=pn,
            location=location,
        )
    assert (
        inventory_state.get_scheduled_demand_status(
            tenant=tenant,
            pn=pn,
            location=location,
        )
        == "unavailable"
    )

    without_open_orders = tmp_path / "without-open-orders"
    to_extract_dir(base, without_open_orders, tenant_id="t1")
    absent_manifest = json.loads(
        (without_open_orders / "manifest.json").read_text()
    )
    assert absent_manifest["artifacts"] == []
    assert not (without_open_orders / "order_plan.json").exists()


def test_mapper_preserves_open_repair_identity_and_lifecycle_evidence(tmp_path) -> None:
    parsed = {
        "parts": [{"part_number": "P1", "part_class": "rotable"}],
        "stock": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "on_hand": "5",
                "in_repair": "1",
            }
        ],
        "open_orders": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "quantity": "1",
                "expected_date": "2026-08-15",
                "order_type": "RO",
                "order_id": "RO-42",
                "order_line_id": "7",
                "vendor_code": "V-9",
                "shop_code": "SHOP-9",
                "opened_at": "2026-07-01T13:15:00Z",
                "status": "IN_PROGRESS",
                "serial_number": "SER-9",
            }
        ],
    }
    out = tmp_path / "open-repair"

    to_extract_dir(
        parsed,
        out,
        tenant_id="t1",
        snapshot_as_of=date(2026, 7, 28),
    )

    rows = json.loads((out / "order_plan.json").read_text())
    assert rows == [
        {
            "hostpartid": "P1",
            "hostlocid": "MIA",
            "planquantity": "1",
            "planrcvdate": "2026-08-15",
            "ordertypeid": "RO",
            "hostorderid": "RO-42",
            "orderlineid": "7",
            "hostvendorlocid": "V-9",
            "hostshopid": "SHOP-9",
            "planorderdate": "2026-07-01T13:15:00Z",
            "orderstatus": "IN_PROGRESS",
            "serialnumber": "SER-9",
        }
    ]


def test_upload_open_orders_use_the_same_safe_type_classification_as_native(
    tmp_path,
    caplog,
) -> None:
    parsed = {
        "parts": [{"part_number": "P1", "part_class": "rotable"}],
        "stock": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "on_hand": "5",
            }
        ],
        "open_orders": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "quantity": "3",
                "expected_date": "2026-08-15",
                "order_id": "PO-LEGACY",
            },
            {
                "part_number": "P1",
                "location_code": "MIA",
                "quantity": "2",
                "expected_date": "",
                "order_id": "RO/LEGACY",
                "status": "IN_PROGRESS",
                "opened_at": "2026-07-01",
            },
            {
                "part_number": "P1",
                "location_code": "MIA",
                "quantity": "11",
                "expected_date": "2026-08-15",
                "order_id": "NO-SAFE-PREFIX",
            },
            {
                "part_number": "P1",
                "location_code": "MIA",
                "quantity": "13",
                "expected_date": "2026-08-15",
                "order_type": "UNKNOWN",
                "order_id": "PO-EXPLICIT-CONFLICT",
            },
        ],
    }
    out = tmp_path / "classified-open-orders"
    to_extract_dir(
        parsed,
        out,
        tenant_id="t1",
        snapshot_as_of=date(2026, 7, 28),
    )

    feature_store, _, _, _ = build_stores_from_extract(out, tenant_id="t1")
    snapshot = feature_store.get_open_orders_snapshot(
        tenant=TenantContext(tenant_id="t1"),
        pn="P1",
        location="MIA",
    )

    assert {
        (order.order_id, order.order_type, order.qty_open)
        for order in snapshot.orders
    } == {
        ("PO-LEGACY", "PO", 3),
        ("RO/LEGACY", "RO", 2),
    }
    assert snapshot.total_open_qty == 5
    assert "excluded 2 open-order row(s) with unclassified order type" in caplog.text


def test_mapper_uses_closed_window_instead_of_observed_nonzero_span(tmp_path) -> None:
    parsed = {
        "parts": [{"part_number": "P1", "part_class": "expendable", "unit_cost": "100"}],
        "stock": [{"part_number": "P1", "location_code": "MIA", "on_hand": "5"}],
        "demand_history": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "period": "2026-01-01",
                "quantity": "7",
            }
        ],
        "demand_window": [
            {
                "observation_start": "2025-11-01",
                "observation_end": "2026-03-31",
            }
        ],
    }
    out = tmp_path / "closed-window"
    to_extract_dir(parsed, out, tenant_id="t1")

    feature_store, _, _, _ = build_stores_from_extract(out, tenant_id="t1")
    history = feature_store.get_demand_history(
        tenant=TenantContext(tenant_id="t1"),
        pn="P1",
        location="MIA",
    )
    stats = historical_demand_stats(history)

    assert stats.trace.observation_window_source == "configured"
    assert stats.trace.observation_start == date(2025, 11, 1)
    assert stats.trace.observation_end == date(2026, 3, 31)
    assert stats.trace.exposure_days == 151
    assert stats.trace.observed_periods == 1
    assert stats.trace.zero_filled_periods == 4
    assert stats.trace.historical_per_day == pytest.approx(7 / 151)


def test_empty_canonical_demand_is_observed_zero_but_omission_is_unavailable(
    tmp_path,
) -> None:
    base = {
        "parts": [{"part_number": "P1", "part_class": "expendable", "unit_cost": "100"}],
        "stock": [{"part_number": "P1", "location_code": "MIA", "on_hand": "5"}],
    }
    observed = tmp_path / "observed-empty"
    to_extract_dir(
        {
            **base,
            "demand_history": [],
            "demand_window": [
                {
                    "observation_start": "2025-11-01",
                    "observation_end": "2026-03-31",
                }
            ],
        },
        observed,
        tenant_id="t1",
    )
    observed_store, _, _, _ = build_stores_from_extract(observed, tenant_id="t1")
    observed_history = observed_store.get_demand_history(
        tenant=TenantContext(tenant_id="t1"),
        pn="P1",
        location="MIA",
    )
    observed_trace = historical_demand_stats(observed_history).trace
    assert observed_history.event_count_source == "observed"
    assert observed_trace.observation_window_source == "configured"
    assert observed_trace.exposure_days == 151
    assert observed_trace.demanded_units == 0
    assert observed_trace.zero_filled_periods == 5

    omitted = tmp_path / "omitted"
    to_extract_dir(base, omitted, tenant_id="t1")
    omitted_store, _, _, _ = build_stores_from_extract(omitted, tenant_id="t1")
    omitted_history = omitted_store.get_demand_history(
        tenant=TenantContext(tenant_id="t1"),
        pn="P1",
        location="MIA",
    )
    assert omitted_history.event_count_source == "unavailable"
    assert historical_demand_stats(omitted_history).trace.observation_window_source == "unavailable"


def test_canonical_requisitions_match_native_snapshot_and_closed_horizon(
    tmp_path,
) -> None:
    parsed = {
        "parts": [{"part_number": "P1", "unit_cost": "100"}],
        "stock": [{"part_number": "P1", "location_code": "MIA", "on_hand": "5"}],
        "requisitions": [
            {
                "requisition_id": "REQ-BOUNDARY",
                "part_number": "P1",
                "location_code": "MIA",
                "quantity": "4",
                "need_by": "2026-05-01",
                "alt_source_location": "ATL",
            },
            {
                "requisition_id": "REQ-AFTER",
                "part_number": "P1",
                "location_code": "MIA",
                "quantity": "9",
                "need_by": "2026-05-02",
            },
            {
                "requisition_id": "REQ-UNDATED",
                "part_number": "P1",
                "location_code": "MIA",
                "quantity": "2",
            },
        ],
    }
    out = tmp_path / "canonical-requisitions"
    to_extract_dir(
        parsed,
        out,
        tenant_id="t1",
        snapshot_as_of=date(2026, 4, 1),
    )

    mapped = json.loads((out / "order_plan_data_requisition.json").read_text())
    assert mapped[0] == {
        "hostorderid": "REQ-BOUNDARY",
        "hostpartid": "P1",
        "hostlocid": "MIA",
        "planquantity": "4",
        "planrcvdate": "2026-05-01",
        "hostreplsourcelocid": "ATL",
        "orderstatus": "OPEN",
        "receivedquantity": "0",
    }

    feature_store, inventory_state, _, _ = build_stores_from_extract(out, tenant_id="t1")
    tenant = TenantContext(tenant_id="t1")
    snapshot = feature_store.get_requisition_snapshot(
        tenant=tenant,
        pn="P1",
        location="MIA",
    )
    scheduled = inventory_state.get_scheduled_demand(
        tenant=tenant,
        pn="P1",
        location="MIA",
    )

    assert snapshot.total_qty_needed == 15
    assert [line.requisition_id for line in snapshot.lines] == [
        "REQ-BOUNDARY",
        "REQ-AFTER",
        "REQ-UNDATED",
    ]
    assert snapshot.lines[0].alt_source_location == "ATL"
    assert inventory_state.get_scheduled_demand_status(
        tenant=tenant,
        pn="P1",
        location="MIA",
    ) == "available"
    assert {item.source_ref for item in scheduled} == {"REQ-BOUNDARY", "REQ-AFTER"}
    assert scheduled_units_in_horizon(
        scheduled,
        as_of=date(2026, 4, 1),
        horizon_days=30,
    ) == 4


def test_empty_canonical_requisitions_are_available_but_omission_is_unavailable(
    tmp_path,
) -> None:
    base = {
        "parts": [{"part_number": "P1", "unit_cost": "100"}],
        "stock": [{"part_number": "P1", "location_code": "MIA", "on_hand": "5"}],
    }
    observed = tmp_path / "observed-empty-requisitions"
    to_extract_dir({**base, "requisitions": []}, observed, tenant_id="t1")
    feature_store, inventory_state, _, _ = build_stores_from_extract(observed, tenant_id="t1")
    tenant = TenantContext(tenant_id="t1")
    assert feature_store.get_requisition_snapshot(
        tenant=tenant,
        pn="P1",
        location="MIA",
    ).lines == []
    assert inventory_state.get_scheduled_demand_status(
        tenant=tenant,
        pn="P1",
        location="MIA",
    ) == "available"

    omitted = tmp_path / "omitted-requisitions"
    to_extract_dir(base, omitted, tenant_id="t1")
    omitted_store, omitted_inventory, _, _ = build_stores_from_extract(
        omitted,
        tenant_id="t1",
    )
    with pytest.raises(FeatureStoreLookupError):
        omitted_store.get_requisition_snapshot(
            tenant=tenant,
            pn="P1",
            location="MIA",
        )
    assert omitted_inventory.get_scheduled_demand_status(
        tenant=tenant,
        pn="P1",
        location="MIA",
    ) == "unavailable"


def test_canonical_parts_cost_synthesizes_default_vendor_and_recommendation(
    tmp_path,
) -> None:
    parsed = {
        "parts": [
            {
                "part_number": "P1",
                "part_class": "expendable",
                "unit_cost": "125.50",
                "criticality": "AOG",
            }
        ],
        "stock": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "on_hand": "0",
                "current_rop": "2",
                "current_eoq": "3",
                "current_safety_stock": "1",
                "current_max": "5",
            }
        ],
        "demand_history": [
            {
                "part_number": "P1",
                "location_code": "MIA",
                "period": "2026-03-01",
                "quantity": "3",
                "observation_start": "2025-04-01",
                "observation_end": "2026-04-01",
            }
        ],
    }
    out = tmp_path / "extract"
    to_extract_dir(parsed, out, tenant_id="t1")

    manifest = json.loads((out / "manifest.json").read_text())
    vendor_artifact = next(
        artifact
        for artifact in manifest["artifacts"]
        if artifact["domain"] == "pn_vendor_price"
    )
    assert vendor_artifact["source"] == "canonical.parts.unit_cost"
    assert vendor_artifact["derived"] is True
    assert vendor_artifact["defaults"] == {
        "vendor": "DEFAULT",
        "minimum_order_qty": 1,
        "lead_time_days": 21,
    }

    feature_store, inventory_state, tenant_id, keys = build_stores_from_extract(
        out,
        tenant_id="t1",
    )
    tenant = TenantContext(tenant_id=tenant_id)
    economics = feature_store.get_vendor_economics(
        tenant=tenant,
        pn="P1",
        vendor="DEFAULT",
    )
    lead_time = feature_store.get_lead_time_distribution(
        tenant=tenant,
        pn="P1",
        vendor="DEFAULT",
        condition="NEW",
    )
    assert str(economics.unit_cost) == "125.50"
    assert economics.minimum_order_qty == 1
    assert lead_time.promised_lead_days == 21

    batch = RecommendationService(
        feature_store=feature_store,
        inventory_state=inventory_state,
    ).run(
        tenant=tenant,
        keys=keys,
        now=datetime(2026, 4, 1),
        as_of=date(2026, 4, 1),
    )
    assert batch.recommendations
    assert not any("vendor_economics" in skipped.reason for skipped in batch.skipped)
