from datetime import UTC, date, datetime

import pytest
from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import (
    Criticality,
    CurrentPolicy,
    FeatureBundle,
    PartAttributes,
    RequisitionLine,
    RequisitionSnapshot,
    StockPosition,
    VendorEconomics,
)
from trax_io_reco.contracts.enums import EvidenceKind
from trax_io_reco.data.inventory_state import InventoryStateLookupError

from trax_io_spine.event_lane.adapters import BundleFeatureStore, BundleInventoryState

ACME = TenantContext(tenant_id="acme")
_D = date(2026, 4, 1)


def _requisition(
    *lines: RequisitionLine,
    tenant_id: str = "acme",
    pn: str = "PN-A",
    location: str = "LOC-1",
) -> RequisitionSnapshot:
    return RequisitionSnapshot(
        tenant_id=tenant_id,
        pn=pn,
        location=location,
        snapshot_at=datetime(2026, 4, 1, tzinfo=UTC),
        lines=list(lines),
        total_qty_needed=sum(line.qty_needed for line in lines),
        extract_date=_D,
    )


def _bundle(
    *,
    tenant_id: str = "acme",
    pn: str = "PN-A",
    location: str = "LOC-1",
    requisition: RequisitionSnapshot | None = None,
) -> FeatureBundle:
    return FeatureBundle(
        tenant_id=tenant_id,
        pn=pn,
        location=location,
        stock_position=StockPosition(
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            on_hand=10,
            serviceable=10,
            extract_date=_D,
        ),
        current_policy=CurrentPolicy(
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            rop=5,
            eoq=4,
            safety_stock=2,
            max_stock=12,
            extract_date=_D,
        ),
        requisition_snapshot=requisition,
        part_attributes=PartAttributes(tenant_id=tenant_id, pn=pn, extract_date=_D),
        criticality=Criticality(
            tenant_id=tenant_id,
            pn=pn,
            raw_essentiality_code="4",
            canonical_tier=4,
            extract_date=_D,
        ),
        vendor_economics={
            "DEFAULT": VendorEconomics(
                tenant_id=tenant_id,
                pn=pn,
                vendor="DEFAULT",
                unit_cost=100,
                extract_date=_D,
            )
        },
    )


def _store() -> BundleFeatureStore:
    return BundleFeatureStore("acme", {("PN-A", "LOC-1"): _bundle()})


def test_serves_key_level_and_part_level_reads() -> None:
    s = _store()
    assert s.get_stock_position(tenant=ACME, pn="PN-A", location="LOC-1").serviceable == 10
    assert s.get_current_policy(tenant=ACME, pn="PN-A", location="LOC-1").rop == 5
    assert s.get_part_attributes(tenant=ACME, pn="PN-A").pn == "PN-A"
    assert s.get_criticality(tenant=ACME, pn="PN-A").canonical_tier == 4
    assert s.get_vendor_economics(tenant=ACME, pn="PN-A", vendor="DEFAULT").unit_cost == 100


def test_missing_field_raises_lookup() -> None:
    # bundle has no demand_history -> miss
    with pytest.raises(FeatureStoreLookupError):
        _store().get_demand_history(tenant=ACME, pn="PN-A", location="LOC-1")


def test_serves_requisition_snapshot_with_tenant_and_key_checks() -> None:
    snapshot = _requisition(
        RequisitionLine(
            requisition_id="REQ-1",
            qty_needed=2,
            need_by=date(2026, 4, 15),
        )
    )
    store = BundleFeatureStore(
        "acme",
        {("PN-A", "LOC-1"): _bundle(requisition=snapshot)},
    )
    assert (
        store.get_requisition_snapshot(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
        )
        == snapshot
    )

    wrong_nested_key = _requisition(
        RequisitionLine(requisition_id="REQ-X", qty_needed=1),
        pn="PN-OTHER",
    )
    corrupt_bundle = _bundle().model_copy(
        update={"requisition_snapshot": wrong_nested_key}
    )
    mismatched = BundleFeatureStore(
        "acme",
        {("PN-A", "LOC-1"): corrupt_bundle},
    )
    with pytest.raises(FeatureStoreLookupError, match="invalid online bundle"):
        mismatched.get_requisition_snapshot(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
        )


def test_bundle_dictionary_key_mismatch_fails_closed() -> None:
    store = BundleFeatureStore(
        "acme",
        {("PN-A", "LOC-1"): _bundle(location="LOC-OTHER")},
    )
    with pytest.raises(FeatureStoreLookupError, match="online bundle identity mismatch"):
        store.get_requisition_snapshot(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
        )


def test_unmodeled_groups_always_miss() -> None:
    with pytest.raises(FeatureStoreLookupError):
        _store().get_causal_utilization(tenant=ACME, ac_type="A320", destination="YYZ")
    with pytest.raises(FeatureStoreLookupError):
        _store().get_wash_rate_history(tenant=ACME, pn="PN-A", location="LOC-1")


def test_cross_tenant_raises() -> None:
    other = TenantContext(tenant_id="other")
    with pytest.raises(FeatureStoreLookupError):
        _store().get_stock_position(tenant=other, pn="PN-A", location="LOC-1")


def test_inventory_state_maps_only_dated_requisitions_to_scheduled_demand() -> None:
    snapshot = _requisition(
        RequisitionLine(
            requisition_id="REQ-LATER",
            qty_needed=4,
            need_by=date(2026, 5, 2),
        ),
        RequisitionLine(
            requisition_id="REQ-UNDATED",
            qty_needed=9,
            need_by=None,
        ),
        RequisitionLine(
            requisition_id="REQ-SOONER",
            qty_needed=2,
            need_by=date(2026, 4, 10),
        ),
    )
    bundles = {("PN-A", "LOC-1"): _bundle(requisition=snapshot)}
    inv = BundleInventoryState("acme", bundles)

    items = inv.get_scheduled_demand(tenant=ACME, pn="PN-A", location="LOC-1")
    assert [(item.source_ref, item.qty, item.due_date) for item in items] == [
        ("REQ-SOONER", 2, date(2026, 4, 10)),
        ("REQ-LATER", 4, date(2026, 5, 2)),
    ]
    assert all(item.source_kind == EvidenceKind.REQUISITION for item in items)
    assert all(item.source_ref != "REQ-UNDATED" for item in items)


def test_inventory_state_missing_requisition_snapshot_is_empty() -> None:
    inv = BundleInventoryState("acme", {("PN-A", "LOC-1"): _bundle()})
    assert inv.get_scheduled_demand(tenant=ACME, pn="PN-A", location="LOC-1") == ()


def test_inventory_state_scheduled_status_follows_requisition_presence() -> None:
    available = BundleInventoryState(
        "acme",
        {("PN-A", "LOC-1"): _bundle(requisition=_requisition())},
    )
    unavailable = BundleInventoryState(
        "acme",
        {("PN-A", "LOC-1"): _bundle()},
    )

    assert available.get_scheduled_demand_status(
        tenant=ACME,
        pn="PN-A",
        location="LOC-1",
    ) == "available"
    assert unavailable.get_scheduled_demand_status(
        tenant=ACME,
        pn="PN-A",
        location="LOC-1",
    ) == "unavailable"
    assert BundleInventoryState().get_scheduled_demand_status(
        tenant=ACME,
        pn="PN-A",
        location="LOC-1",
    ) == "unavailable"


def test_inventory_state_scheduled_status_fails_closed_on_nested_identity() -> None:
    wrong_nested_key = _requisition(pn="PN-OTHER")
    corrupt_bundle = _bundle().model_copy(
        update={"requisition_snapshot": wrong_nested_key}
    )
    inventory_state = BundleInventoryState(
        "acme",
        {("PN-A", "LOC-1"): corrupt_bundle},
    )

    with pytest.raises(
        InventoryStateLookupError,
        match="invalid online bundle",
    ):
        inventory_state.get_scheduled_demand_status(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
        )


def test_inventory_state_cross_tenant_fails_closed() -> None:
    inv = BundleInventoryState("acme", {("PN-A", "LOC-1"): _bundle()})
    with pytest.raises(InventoryStateLookupError, match="provider is acme"):
        inv.get_scheduled_demand(
            tenant=TenantContext(tenant_id="other"),
            pn="PN-A",
            location="LOC-1",
        )


def test_event_lane_rejects_corrupt_non_requisition_nested_identity() -> None:
    valid = _bundle()
    wrong_stock = valid.stock_position.model_copy(
        update={"tenant_id": "other"}
    )
    corrupt = valid.model_copy(update={"stock_position": wrong_stock})

    feature_store = BundleFeatureStore(
        "acme",
        {("PN-A", "LOC-1"): corrupt},
    )
    with pytest.raises(FeatureStoreLookupError, match="invalid online bundle"):
        feature_store.get_stock_position(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
        )

    inventory_state = BundleInventoryState(
        "acme",
        {("PN-A", "LOC-1"): corrupt},
    )
    with pytest.raises(InventoryStateLookupError, match="invalid online bundle"):
        inventory_state.get_scheduled_demand_status(
            tenant=ACME,
            pn="PN-A",
            location="LOC-1",
        )


def test_legacy_inventory_state_constructor_remains_data_free() -> None:
    inv = BundleInventoryState()
    assert inv.get_scheduled_demand(tenant=ACME, pn="PN-A", location="LOC-1") == ()
    assert inv.get_aog_signal(tenant=ACME, pn="PN-A", location="LOC-1") is not None
    assert inv.get_repair_tat(tenant=ACME, pn="PN-A") is not None
