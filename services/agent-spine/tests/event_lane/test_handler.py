from datetime import UTC, date, datetime

import pytest
from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import (
    FeatureBundle,
    RequisitionLine,
    RequisitionSnapshot,
    StockPosition,
)

from trax_io_spine.contracts import OrchestrationResult
from trax_io_spine.event_lane.events import (
    DomainEvent,
    EventKind,
    RemovalRecordedPayload,
)
from trax_io_spine.event_lane.online import InMemoryOnlineStore

ACME = TenantContext(tenant_id="acme")
_D = date(2026, 4, 1)


def _bundle(
    loc: str,
    *,
    requisition_snapshot: RequisitionSnapshot | None = None,
) -> FeatureBundle:
    return FeatureBundle(
        tenant_id="acme",
        pn="PN-A",
        location=loc,
        stock_position=StockPosition(
            tenant_id="acme",
            pn="PN-A",
            location=loc,
            on_hand=5,
            serviceable=5,
            extract_date=_D,
        ),
        requisition_snapshot=requisition_snapshot,
    )


def test_in_memory_online_store_get_and_miss() -> None:
    store = InMemoryOnlineStore([_bundle("LOC-1")])
    assert store.get_bundle(tenant=ACME, pn="PN-A", location="LOC-1").location == "LOC-1"
    with pytest.raises(FeatureStoreLookupError):
        store.get_bundle(tenant=ACME, pn="PN-A", location="NOPE")
    with pytest.raises(FeatureStoreLookupError):
        store.get_bundle(tenant=TenantContext(tenant_id="other"), pn="PN-A", location="LOC-1")


def test_handler_empty_keys_returns_empty_result() -> None:
    from trax_io_spine.event_lane.handler import EventLaneHandler
    from trax_io_spine.writeback.target import InMemoryWritebackTarget

    handler = EventLaneHandler(
        online_store=InMemoryOnlineStore([_bundle("LOC-1")]),
        writeback=InMemoryWritebackTarget(),
    )
    # eo_published is a fan-out event -> DirectKeyResolver returns empty -> no recompute
    from trax_io_spine.event_lane.events import EoPublishedPayload

    ev = DomainEvent(
        tenant_id="acme",
        kind=EventKind.EO_PUBLISHED,
        occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload=EoPublishedPayload(eo_number="EO-1", ata_chapter="32", affected_fleet="A320"),
    )
    res = handler.handle(ev)
    assert res.summary["recommendations"] == 0
    assert res.written == () and res.queued == ()


def test_handler_passes_tenant_bound_bundles_to_inventory_state(monkeypatch) -> None:
    import trax_io_spine.event_lane.handler as handler_module

    snapshot = RequisitionSnapshot(
        tenant_id="acme",
        pn="PN-A",
        location="LOC-1",
        snapshot_at=datetime(2026, 4, 1, tzinfo=UTC),
        lines=[
            RequisitionLine(
                requisition_id="REQ-HANDLER",
                qty_needed=3,
                need_by=date(2026, 4, 20),
            )
        ],
        total_qty_needed=3,
        extract_date=_D,
    )
    captured = {}

    class CapturingSupervisor:
        def __init__(self, *, inventory_state, **_kwargs) -> None:
            captured["inventory_state"] = inventory_state

        def run(self, *, tenant, keys, now) -> OrchestrationResult:
            pn, location = keys[0]
            captured["scheduled"] = captured["inventory_state"].get_scheduled_demand(
                tenant=tenant,
                pn=pn,
                location=location,
            )
            return OrchestrationResult(
                tenant_id=tenant.tenant_id,
                generated_at=now,
                summary={"recommendations": 0},
            )

    monkeypatch.setattr(handler_module, "Supervisor", CapturingSupervisor)
    handler = handler_module.EventLaneHandler(
        online_store=InMemoryOnlineStore([_bundle("LOC-1", requisition_snapshot=snapshot)]),
        writeback=object(),
    )
    event = DomainEvent(
        tenant_id="acme",
        kind=EventKind.REMOVAL_RECORDED,
        occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload=RemovalRecordedPayload(
            pn="PN-A",
            tail="N123",
            location="LOC-1",
        ),
    )

    handler.handle(event)

    assert len(captured["scheduled"]) == 1
    item = captured["scheduled"][0]
    assert item.source_ref == "REQ-HANDLER"
    assert item.qty == 3
    assert item.due_date == date(2026, 4, 20)
