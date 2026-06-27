"""Resolve a domain event to the (pn, location) keys to recompute.

v1 handles the events that name a (pn, location) directly. Fan-out events (eo_published by ATA,
vendor_price_changed pn-wide, flight_completed by AC-type, plan_published by fleet, wo_scheduled
which carries no pn) need catalog enumeration / a BOM lookup and resolve to an empty set here —
a production KeyResolver plugs into the same Protocol.
"""

from __future__ import annotations

from typing import Protocol

from trax_io_spine.event_lane.events import (
    DomainEvent,
    EventKind,
    RemovalRecordedPayload,
    StockMovedPayload,
)


class KeyResolver(Protocol):
    def resolve(self, event: DomainEvent) -> set[tuple[str, str]]: ...


class DirectKeyResolver:
    """Resolves only the events whose payload names an explicit (pn, location)."""

    def resolve(self, event: DomainEvent) -> set[tuple[str, str]]:
        if event.kind is EventKind.STOCK_MOVED and isinstance(event.payload, StockMovedPayload):
            p = event.payload
            return {(p.pn, p.from_location), (p.pn, p.to_location)}
        if event.kind is EventKind.REMOVAL_RECORDED and isinstance(
            event.payload, RemovalRecordedPayload
        ):
            p = event.payload
            return {(p.pn, p.location)}
        return set()
