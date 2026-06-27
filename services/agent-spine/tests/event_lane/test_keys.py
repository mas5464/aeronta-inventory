from datetime import UTC, datetime

from trax_io_spine.event_lane.events import (
    DomainEvent,
    EoPublishedPayload,
    EventKind,
    RemovalRecordedPayload,
    StockMovedPayload,
)
from trax_io_spine.event_lane.keys import DirectKeyResolver

_NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _ev(kind: EventKind, payload: object) -> DomainEvent:
    return DomainEvent(tenant_id="acme", kind=kind, occurred_at=_NOW, payload=payload)


def test_stock_moved_resolves_both_endpoints() -> None:
    ev = _ev(EventKind.STOCK_MOVED,
             StockMovedPayload(pn="PN-A", from_location="LOC-1", to_location="LOC-2", qty=3))
    assert DirectKeyResolver().resolve(ev) == {("PN-A", "LOC-1"), ("PN-A", "LOC-2")}


def test_removal_recorded_resolves_one_key() -> None:
    ev = _ev(EventKind.REMOVAL_RECORDED,
             RemovalRecordedPayload(pn="PN-A", tail="C-FABC", location="LOC-1"))
    assert DirectKeyResolver().resolve(ev) == {("PN-A", "LOC-1")}


def test_fan_out_event_resolves_empty_in_v1() -> None:
    ev = _ev(EventKind.EO_PUBLISHED,
             EoPublishedPayload(eo_number="EO-1", ata_chapter="32", affected_fleet="A320"))
    assert DirectKeyResolver().resolve(ev) == set()
