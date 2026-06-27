"""Domain event contracts — the seven eMRO Outbound Event Publisher events (design §4.1).

Promoted from the original 2026-04-14 agent-spine plan. The schema is contract-first and
semver-governed (``schema_version``); the event lane consumes these in-process.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class EventKind(StrEnum):
    FLIGHT_COMPLETED = "flight_completed"
    STOCK_MOVED = "stock_moved"
    WO_SCHEDULED = "wo_scheduled"
    VENDOR_PRICE_CHANGED = "vendor_price_changed"
    PLAN_PUBLISHED = "plan_published"
    REMOVAL_RECORDED = "removal_recorded"
    EO_PUBLISHED = "eo_published"


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FlightCompletedPayload(_Payload):
    tail: str
    ac_type: str
    destination: str
    flight_hours: float = 0.0
    cycles: int = 0


class StockMovedPayload(_Payload):
    pn: str
    from_location: str
    to_location: str
    qty: int


class WoScheduledPayload(_Payload):
    wo: str
    location: str
    scheduled_start: datetime
    tail: str | None = None


class VendorPriceChangedPayload(_Payload):
    pn: str
    vendor: str
    old_price: float
    new_price: float
    lead_days: int


class PlanPublishedPayload(_Payload):
    plan_id: str
    fleet: str
    horizon_days: int


class RemovalRecordedPayload(_Payload):
    pn: str
    tail: str
    location: str
    removal_reason: str = ""


class EoPublishedPayload(_Payload):
    eo_number: str
    ata_chapter: str
    affected_fleet: str
    criticality: Literal["AD", "SB", "FLEET_CAMPAIGN", "OTHER"] = "OTHER"


Payload = (
    FlightCompletedPayload
    | StockMovedPayload
    | WoScheduledPayload
    | VendorPriceChangedPayload
    | PlanPublishedPayload
    | RemovalRecordedPayload
    | EoPublishedPayload
)


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    kind: EventKind
    occurred_at: datetime
    payload: Payload
    schema_version: str = "1.0.0"
    event_id: str | None = None
