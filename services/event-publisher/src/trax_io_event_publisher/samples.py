"""Valid-by-construction sample events — the contract test oracle."""

from __future__ import annotations

from datetime import UTC, date, datetime

from trax_io_event_publisher.ids import new_event_id
from trax_io_event_publisher.schemas import (
    EoPublishedPayload,
    EventEnvelope,
    EventKind,
    FlightCompletedPayload,
    PlanPublishedPayload,
    Producer,
    RemovalRecordedPayload,
    StockMovedPayload,
    VendorPriceChangedPayload,
    WoScheduledPayload,
)

_T = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
_D = date(2026, 4, 1)
_PRODUCER = Producer(system="emro", version="2026.4.0", instance="lhr-1")

_PAYLOADS = {
    EventKind.FLIGHT_COMPLETED: lambda: FlightCompletedPayload(
        tail="N123AA", ac_type="A320", destination="LHR", origin="JFK",
        flight_hours=7.5, cycles=1, flight_date=_D,
    ),
    EventKind.STOCK_MOVED: lambda: StockMovedPayload(
        pn="A320-WHEEL", sn="SN1", from_location="JFK", to_location="LHR",
        from_condition="SVC", to_condition="SVC", qty=1,
        transaction_type="TRANSFER", transaction_no=88412, wo="WO1", moved_by="op1",
    ),
    EventKind.WO_SCHEDULED: lambda: WoScheduledPayload(
        wo="WO1", tail="N123AA", ac_type="A320", location="LHR", wo_type="LINE",
        scheduled_start=_T, scheduled_end=_T, estimated_duration_days=2.0, primary_eo="EO1",
    ),
    EventKind.VENDOR_PRICE_CHANGED: lambda: VendorPriceChangedPayload(
        pn="A320-WHEEL", vendor="ACME", condition="NEW", old_price=100.0, new_price=120.0,
        currency="USD", old_lead_days=30, new_lead_days=21, preferred=True, effective_date=_D,
    ),
    EventKind.PLAN_PUBLISHED: lambda: PlanPublishedPayload(
        plan_id="P1", plan_type="MAINT", fleet="A320", horizon_days=90,
        effective_from=_D, revision=1,
    ),
    EventKind.REMOVAL_RECORDED: lambda: RemovalRecordedPayload(
        pn="A320-WHEEL", sn="SN1", tail="N123AA", ac_type="A320", location="LHR",
        wo="WO1", task_card="TC1", removal_reason="worn", schedule_category="UNSCHEDULED",
        reason_category="WEAR", removed_at=_T,
    ),
    EventKind.EO_PUBLISHED: lambda: EoPublishedPayload(
        eo_number="EO1", ata_chapter="32", ata_subchapter="32-40", affected_fleet="A320",
        affected_pn_pattern="A320-%", criticality="AD", compliance_due=_D,
        compliance_threshold_hours=500.0, compliance_threshold_cycles=200,
        issued_by="eng1", title="Wheel AD", issued_at=_T,
    ),
}


def make_event(kind: EventKind | str, **overrides) -> EventEnvelope:
    kind = EventKind(kind)
    fields = dict(
        event_id=new_event_id(),
        tenant_id="acme-air",
        kind=kind,
        occurred_at=_T,
        produced_at=_T,
        producer=_PRODUCER,
        payload=_PAYLOADS[kind](),
    )
    fields.update(overrides)
    return EventEnvelope(**fields)
