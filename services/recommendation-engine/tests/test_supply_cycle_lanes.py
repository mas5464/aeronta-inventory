"""Black-box contracts for independent procurement and repair-cycle lanes."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError
from trax_io_feature_store import FeatureStoreLookupError, InMemoryFeatureStore, TenantContext
from trax_io_feature_store.schemas import OpenOrder, OpenOrdersSnapshot

from tests.fixtures.builders import lead_time, seed_part
from tests.fixtures.extract_fixture import write_sample_extract
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.policy.lead_time import lead_mean_var
from trax_io_reco.position.net_position import expected_receipts


def _write(extract_dir, domain: str, rows: list[dict]) -> None:  # type: ignore[no-untyped-def]
    (extract_dir / f"{domain}.json").write_text(json.dumps(rows))


def _closed(
    *,
    order_type: str | None,
    order_id: str,
    pn: str,
    vendor: str,
    days: int,
) -> dict:
    row = {
        "hostorderid": order_id,
        "orderid": order_id,
        "hostpartid": pn,
        "hostvendorlocid": vendor,
        "planorderdate": "2026-01-01",
        "actualrcvdate": (date(2026, 1, 1) + timedelta(days=days)).isoformat(),
    }
    if order_type is not None:
        row["ordertypeid"] = order_type
    return row


def test_mixed_po_ro_rows_materialize_exact_independent_distributions(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    pn = "HYD-PUMP-001"
    _write(
        extract_dir,
        "pn_vendor_price",
        [
            {
                "hostpartid": pn,
                "hostvendorlocid": "BUY-VENDOR",
                "ordertypeid": "PO",
                "condition": "NEW",
                "price": "100",
                "processinglength": "30",
                "preferred": "Y",
                "minoq": "1",
            }
        ],
    )
    _write(
        extract_dir,
        "order_plan_closed_orders",
        [
            *[
                _closed(
                    order_type="PO",
                    order_id=f"PO_{index}_1",
                    pn=pn,
                    vendor="BUY-VENDOR",
                    days=days,
                )
                for index, days in enumerate((10, 20, 30, 40), start=1)
            ],
            *[
                _closed(
                    order_type="RO",
                    order_id=f"RO_{index}_1",
                    pn=pn,
                    vendor="REPAIR-SHOP",
                    days=days,
                )
                for index, days in enumerate((5, 15, 25), start=1)
            ],
        ],
    )

    store, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    tenant = TenantContext(tenant_id=tenant_id)
    procurement = store.get_lead_time_distribution(
        tenant=tenant,
        pn=pn,
        vendor="BUY-VENDOR",
        condition="NEW",
    )
    repair = store.get_lead_time_distribution(
        tenant=tenant,
        pn=pn,
        vendor="REPAIR-SHOP",
        condition="REP",
    )

    assert (
        procurement.realized_mean_days,
        procurement.realized_p50_days,
        procurement.realized_p90_days,
        procurement.realized_p99_days,
        procurement.n_observations,
    ) == pytest.approx((25, 30, 40, 40, 4))
    assert (
        repair.realized_mean_days,
        repair.realized_p50_days,
        repair.realized_p90_days,
        repair.realized_p99_days,
        repair.n_observations,
    ) == pytest.approx((15, 15, 25, 25, 3))
    assert procurement.classification_source == "explicit_order_type"
    assert procurement.evidence_status == "observed"
    assert procurement.source == "order_plan_closed_orders"
    assert procurement.grouping_level == "part_vendor_condition"
    assert procurement.confidence == "low"
    assert procurement.data_cutoff == date(2026, 2, 10)
    assert procurement.model_version == "supply-cycle-v2"
    assert procurement.observed_cycle_days == (10, 20, 30, 40)
    assert repair.classification_source == "explicit_order_type"
    assert repair.evidence_status == "observed"
    assert repair.source == "order_plan_closed_orders"
    assert repair.grouping_level == "part_vendor_condition"
    assert repair.confidence == "low"
    assert repair.data_cutoff == date(2026, 1, 26)
    assert repair.model_version == "supply-cycle-v2"
    assert repair.observed_cycle_days == (5, 15, 25)
    assert repair.proxy_definition == "order_creation_to_last_receipt"


def test_closed_only_lanes_survive_without_price_rows(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    pn = "HYD-PUMP-001"
    _write(extract_dir, "pn_vendor_price", [])
    _write(
        extract_dir,
        "order_plan_closed_orders",
        [
            _closed(
                order_type="PO",
                order_id="PO_10_1",
                pn=pn,
                vendor="BUY-VENDOR",
                days=12,
            ),
            _closed(
                order_type="RO",
                order_id="RO_20_1",
                pn=pn,
                vendor="REPAIR-SHOP",
                days=35,
            ),
        ],
    )

    store, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    tenant = TenantContext(tenant_id=tenant_id)
    procurement = store.get_lead_time_distribution(
        tenant=tenant,
        pn=pn,
        vendor="BUY-VENDOR",
        condition="NEW",
    )
    repair = store.get_lead_time_distribution(
        tenant=tenant,
        pn=pn,
        vendor="REPAIR-SHOP",
        condition="REP",
    )

    assert procurement.evidence_status == repair.evidence_status == "observed"
    assert procurement.promised_lead_days is None
    assert procurement.promised_vs_actual_delta_mean is None
    assert repair.promised_lead_days is None
    assert repair.proxy_definition == "order_creation_to_last_receipt"
    assert repair.model_version == "supply-cycle-v2"
    assert repair.observed_cycle_days == (35,)


def test_price_only_rows_are_typed_lane_fallbacks_and_rep_never_wins_new(
    tmp_path,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    pn = "HYD-PUMP-001"
    _write(extract_dir, "order_plan_closed_orders", [])
    _write(
        extract_dir,
        "pn_vendor_price",
        [
            {
                "hostpartid": pn,
                "hostvendorlocid": "REPAIR-SHOP",
                "ordertypeid": "RO",
                "condition": "REP",
                "price": "1",
                "processinglength": "90",
                "preferred": "Y",
                "minoq": "1",
            },
            {
                "hostpartid": pn,
                "hostvendorlocid": "BUY-VENDOR",
                "ordertypeid": "PO",
                "condition": "NEW",
                "price": "125",
                "processinglength": "20",
                "preferred": "N",
                "minoq": "2",
            },
        ],
    )

    store, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    tenant = TenantContext(tenant_id=tenant_id)
    procurement = store.get_lead_time_distribution(
        tenant=tenant,
        pn=pn,
        vendor="DEFAULT",
        condition="NEW",
    )
    repair = store.get_lead_time_distribution(
        tenant=tenant,
        pn=pn,
        vendor="DEFAULT",
        condition="REP",
    )
    economics = store.get_vendor_economics(
        tenant=tenant,
        pn=pn,
        vendor="DEFAULT",
    )

    assert procurement.evidence_status == "configured_fallback"
    assert procurement.source == "pn_vendor_price"
    assert procurement.classification_source == "explicit_order_type"
    assert procurement.n_observations == 0
    assert procurement.realized_mean_days == procurement.realized_p99_days == 20
    assert repair.evidence_status == "configured_fallback"
    assert repair.promised_lead_days == 90
    assert repair.proxy_definition == "configured_repair_promise"
    assert str(economics.unit_cost) == "125"


def test_blank_legacy_price_condition_is_labeled_as_legacy_default_new(
    tmp_path,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    pn = "HYD-PUMP-001"
    _write(extract_dir, "order_plan_closed_orders", [])
    _write(
        extract_dir,
        "pn_vendor_price",
        [
            {
                "hostpartid": pn,
                "hostvendorlocid": "LEGACY-VENDOR",
                "price": "125",
                "processinglength": "20",
                "preferred": "Y",
                "minoq": "1",
            }
        ],
    )

    store, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    procurement = store.get_lead_time_distribution(
        tenant=TenantContext(tenant_id=tenant_id),
        pn=pn,
        vendor="LEGACY-VENDOR",
        condition="NEW",
    )

    assert procurement.evidence_status == "configured_fallback"
    assert procurement.classification_source == "legacy_default_new"


def test_failed_lane_feeds_are_absent_even_when_stale_files_exist(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    pn = "HYD-PUMP-001"
    _write(
        extract_dir,
        "order_plan_closed_orders",
        [
            _closed(
                order_type="PO",
                order_id="PO_STALE_1",
                pn=pn,
                vendor="BUY-VENDOR",
                days=12,
            )
        ],
    )
    manifest_path = extract_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for artifact in manifest["artifacts"]:
        if artifact["domain"] in {"pn_vendor_price", "order_plan_closed_orders"}:
            artifact["status"] = "failed"
    manifest_path.write_text(json.dumps(manifest))

    store, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    tenant = TenantContext(tenant_id=tenant_id)
    for condition in ("NEW", "REP"):
        with pytest.raises(FeatureStoreLookupError):
            store.get_lead_time_distribution(
                tenant=tenant,
                pn=pn,
                vendor="DEFAULT",
                condition=condition,
            )


def test_legacy_id_prefix_is_fallback_but_explicit_order_type_is_authoritative(
    tmp_path,
) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    pn = "HYD-PUMP-001"
    _write(extract_dir, "pn_vendor_price", [])
    _write(
        extract_dir,
        "order_plan_closed_orders",
        [
            _closed(
                order_type=None,
                order_id="PO_LEGACY_1",
                pn=pn,
                vendor="DEFAULT",
                days=10,
            ),
            _closed(
                order_type=None,
                order_id="RO_LEGACY_1",
                pn=pn,
                vendor="DEFAULT",
                days=20,
            ),
            # The explicit classifier wins over the conflicting legacy prefix.
            _closed(
                order_type="RO",
                order_id="PO_CONFLICT_1",
                pn=pn,
                vendor="DEFAULT",
                days=30,
            ),
        ],
    )

    store, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    tenant = TenantContext(tenant_id=tenant_id)
    procurement = store.get_lead_time_distribution(
        tenant=tenant,
        pn=pn,
        vendor="DEFAULT",
        condition="NEW",
    )
    repair = store.get_lead_time_distribution(
        tenant=tenant,
        pn=pn,
        vendor="DEFAULT",
        condition="REP",
    )

    assert procurement.n_observations == 1
    assert procurement.realized_mean_days == 10
    assert procurement.classification_source == "legacy_order_id_prefix"
    assert repair.n_observations == 2
    assert repair.realized_mean_days == 25
    assert repair.classification_source == "legacy_order_id_prefix"


def test_assembler_resolves_procurement_from_po_and_repair_from_ro() -> None:
    feature_store = InMemoryFeatureStore()
    inventory = InMemoryInventoryState()
    seed_part(
        feature_store,
        inventory,
        tenant_id="acme",
        pn="P-1",
        location="YYZ",
        monthly_units=[1, 2],
        vendor="BUY-VENDOR",
    )
    feature_store.seed(
        "acme",
        "lead_time_distribution",
        ("P-1", "REPAIR-SHOP", "REP"),
        lead_time(
            tenant_id="acme",
            pn="P-1",
            vendor="REPAIR-SHOP",
            mean_days=45,
            condition="REP",
        ),
    )
    feature_store.seed(
        "acme",
        "open_orders_snapshot",
        ("P-1", "YYZ"),
        OpenOrdersSnapshot(
            tenant_id="acme",
            pn="P-1",
            location="YYZ",
            snapshot_at=datetime(2026, 4, 1),
            orders=[
                OpenOrder(
                    order_id="RO_1_1",
                    order_type="RO",
                    vendor="REPAIR-SHOP",
                    qty_open=1,
                    expected_rcv_date=date(2026, 4, 2),
                ),
                OpenOrder(
                    order_id="PO_2_1",
                    order_type="PO",
                    vendor="BUY-VENDOR",
                    qty_open=1,
                    expected_rcv_date=date(2026, 4, 10),
                ),
            ],
            total_open_qty=2,
            extract_date=date(2026, 4, 1),
        ),
    )

    context = ContextAssembler(
        features=FeatureReader(feature_store),
        inventory_state=inventory,
    ).assemble(
        tenant=TenantContext(tenant_id="acme"),
        pn="P-1",
        location="YYZ",
    )

    assert context.vendor_economics.vendor == "BUY-VENDOR"
    assert context.lead_time is not None
    assert context.lead_time.vendor == "BUY-VENDOR"
    assert context.repair_cycle_time is not None
    assert context.repair_cycle_time.vendor == "REPAIR-SHOP"


def test_assembler_falls_back_to_default_economics_for_missing_po_vendor() -> None:
    feature_store = InMemoryFeatureStore()
    inventory = InMemoryInventoryState()
    seed_part(
        feature_store,
        inventory,
        tenant_id="acme",
        pn="P-1",
        location="YYZ",
        monthly_units=[1, 2],
        vendor="DEFAULT",
    )
    feature_store.seed(
        "acme",
        "open_orders_snapshot",
        ("P-1", "YYZ"),
        OpenOrdersSnapshot(
            tenant_id="acme",
            pn="P-1",
            location="YYZ",
            snapshot_at=datetime(2026, 4, 1),
            orders=[
                OpenOrder(
                    order_id="PO_1_1",
                    order_type="PO",
                    vendor="BUY-VENDOR-WITHOUT-PRICE",
                    qty_open=1,
                    expected_rcv_date=date(2026, 4, 10),
                ),
                OpenOrder(
                    order_id="RO_1_1",
                    order_type="RO",
                    vendor="REPAIR-SHOP",
                    qty_open=1,
                    expected_rcv_date=date(2026, 4, 2),
                ),
            ],
            total_open_qty=2,
            extract_date=date(2026, 4, 1),
        ),
    )

    context = ContextAssembler(
        features=FeatureReader(feature_store),
        inventory_state=inventory,
    ).assemble(
        tenant=TenantContext(tenant_id="acme"),
        pn="P-1",
        location="YYZ",
    )

    assert context.vendor_economics.vendor == "DEFAULT"


def test_part_context_rejects_crossed_supply_cycle_lanes() -> None:
    feature_store = InMemoryFeatureStore()
    inventory = InMemoryInventoryState()
    seed_part(
        feature_store,
        inventory,
        tenant_id="acme",
        pn="P-1",
        location="YYZ",
        monthly_units=[1, 2],
    )
    context = ContextAssembler(
        features=FeatureReader(feature_store),
        inventory_state=inventory,
    ).assemble(
        tenant=TenantContext(tenant_id="acme"),
        pn="P-1",
        location="YYZ",
    )
    payload = context.model_dump()
    payload["lead_time"] = lead_time(
        tenant_id="acme",
        pn="P-1",
        condition="REP",
    ).model_dump()

    with pytest.raises(ValidationError, match="procurement NEW"):
        type(context).model_validate(payload)

    payload = context.model_dump()
    payload["repair_cycle_time"] = lead_time(
        tenant_id="acme",
        pn="P-1",
        condition="NEW",
    ).model_dump()
    with pytest.raises(ValidationError, match="repair REP"):
        type(context).model_validate(payload)


def test_repair_cycle_is_descriptive_and_procurement_policy_invariant() -> None:
    feature_store = InMemoryFeatureStore()
    inventory = InMemoryInventoryState()
    seed_part(
        feature_store,
        inventory,
        tenant_id="acme",
        pn="P-1",
        location="YYZ",
        monthly_units=[1, 2],
        lead_mean_days=20,
        in_repair=8,
    )
    assembler = ContextAssembler(
        features=FeatureReader(feature_store),
        inventory_state=inventory,
    )

    feature_store.seed(
        "acme",
        "lead_time_distribution",
        ("P-1", "DEFAULT", "REP"),
        lead_time(
            tenant_id="acme",
            pn="P-1",
            mean_days=30,
            condition="REP",
        ),
    )
    first = assembler.assemble(
        tenant=TenantContext(tenant_id="acme"),
        pn="P-1",
        location="YYZ",
    )
    feature_store.seed(
        "acme",
        "lead_time_distribution",
        ("P-1", "DEFAULT", "REP"),
        lead_time(
            tenant_id="acme",
            pn="P-1",
            mean_days=300,
            condition="REP",
        ),
    )
    second = assembler.assemble(
        tenant=TenantContext(tenant_id="acme"),
        pn="P-1",
        location="YYZ",
    )

    assert lead_mean_var(first) == lead_mean_var(second)
    assert expected_receipts(
        open_orders=None,
        repair_tat=second.repair_tat,
        stock_position=second.stock_position,
        window_days=365,
        as_of=date(2026, 4, 1),
    ) == 0
