"""Down-project canonical EventEnvelope to the slim DomainEvent consumed by the event lane.

This is a one-way reconciliation: the slim payload fields are a strict subset of the
canonical fields for every kind. The canonical schema (trax_io_event_publisher) is the
single source of truth; the slim models here are never edited.
"""

from __future__ import annotations

from trax_io_event_publisher.schemas import (
    EoPublishedPayload as CanonEoPublished,
)
from trax_io_event_publisher.schemas import (
    EventEnvelope,
)
from trax_io_event_publisher.schemas import (
    EventKind as CanonKind,
)
from trax_io_event_publisher.schemas import (
    FlightCompletedPayload as CanonFlightCompleted,
)
from trax_io_event_publisher.schemas import (
    PlanPublishedPayload as CanonPlanPublished,
)
from trax_io_event_publisher.schemas import (
    RemovalRecordedPayload as CanonRemovalRecorded,
)
from trax_io_event_publisher.schemas import (
    StockMovedPayload as CanonStockMoved,
)
from trax_io_event_publisher.schemas import (
    VendorPriceChangedPayload as CanonVendorPriceChanged,
)
from trax_io_event_publisher.schemas import (
    WoScheduledPayload as CanonWoScheduled,
)

from trax_io_spine.event_lane.events import (
    DomainEvent,
    EoPublishedPayload,
    FlightCompletedPayload,
    PlanPublishedPayload,
    RemovalRecordedPayload,
    StockMovedPayload,
    VendorPriceChangedPayload,
    WoScheduledPayload,
)
from trax_io_spine.event_lane.events import (
    EventKind as SlimKind,
)


def _to_flight_completed_payload(p: CanonFlightCompleted) -> FlightCompletedPayload:
    return FlightCompletedPayload(
        tail=p.tail,
        ac_type=p.ac_type,
        destination=p.destination,
        flight_hours=p.flight_hours,
        cycles=p.cycles,
    )


def _to_stock_moved_payload(p: CanonStockMoved) -> StockMovedPayload:
    return StockMovedPayload(
        pn=p.pn,
        from_location=p.from_location,
        to_location=p.to_location,
        qty=p.qty,
    )


def _to_wo_scheduled_payload(p: CanonWoScheduled) -> WoScheduledPayload:
    return WoScheduledPayload(
        wo=p.wo,
        location=p.location,
        scheduled_start=p.scheduled_start,
        tail=p.tail,
    )


def _to_vendor_price_changed_payload(p: CanonVendorPriceChanged) -> VendorPriceChangedPayload:
    return VendorPriceChangedPayload(
        pn=p.pn,
        vendor=p.vendor,
        old_price=p.old_price,
        new_price=p.new_price,
        lead_days=p.new_lead_days,
    )


def _to_plan_published_payload(p: CanonPlanPublished) -> PlanPublishedPayload:
    return PlanPublishedPayload(
        plan_id=p.plan_id,
        fleet=p.fleet,
        horizon_days=p.horizon_days,
    )


def _to_removal_recorded_payload(p: CanonRemovalRecorded) -> RemovalRecordedPayload:
    return RemovalRecordedPayload(
        pn=p.pn,
        tail=p.tail,
        location=p.location,
        removal_reason=p.removal_reason,
    )


def _to_eo_published_payload(p: CanonEoPublished) -> EoPublishedPayload:
    return EoPublishedPayload(
        eo_number=p.eo_number,
        ata_chapter=p.ata_chapter,
        affected_fleet=p.affected_fleet,
        criticality=p.criticality,
    )


_ADAPTERS = {
    CanonKind.FLIGHT_COMPLETED: _to_flight_completed_payload,
    CanonKind.STOCK_MOVED: _to_stock_moved_payload,
    CanonKind.WO_SCHEDULED: _to_wo_scheduled_payload,
    CanonKind.VENDOR_PRICE_CHANGED: _to_vendor_price_changed_payload,
    CanonKind.PLAN_PUBLISHED: _to_plan_published_payload,
    CanonKind.REMOVAL_RECORDED: _to_removal_recorded_payload,
    CanonKind.EO_PUBLISHED: _to_eo_published_payload,
}


def to_domain_event(canonical: EventEnvelope) -> DomainEvent:
    """Down-project a canonical EventEnvelope to the slim DomainEvent."""
    adapter = _ADAPTERS.get(canonical.kind)
    if adapter is None:
        raise ValueError(f"unknown canonical kind: {canonical.kind!r}")  # defensive, unreachable
    slim_payload = adapter(canonical.payload)  # type: ignore[arg-type]
    return DomainEvent(
        tenant_id=canonical.tenant_id,
        event_id=canonical.event_id,
        kind=SlimKind(canonical.kind.value),
        occurred_at=canonical.occurred_at,
        schema_version=canonical.schema_version,
        payload=slim_payload,
    )
