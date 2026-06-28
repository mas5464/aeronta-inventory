"""Canonical eMRO outbound-event wire contract (single source of truth).

Mirrors docs/contracts/2026-04-14-emro-event-publisher-contract.md field-for-field.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trax_io_event_publisher.ids import is_uuid7

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WS = re.compile(r"\s+")

UNTRUSTED_FIELDS = frozenset(
    {"removal_recorded.removal_reason", "eo_published.title"}
)


def scrub(text: str) -> str:
    """Baseline neutralization for untrusted free-text (full policy is #4's)."""
    no_ctrl = _CONTROL.sub(" ", text)
    collapsed = _WS.sub(" ", no_ctrl).strip()
    return collapsed[:500]


def schema_version_compatible(consumer_major: int, event_version: str) -> bool:
    if not _SEMVER.match(event_version):
        return False
    return int(event_version.split(".")[0]) == consumer_major


class EventKind(StrEnum):
    FLIGHT_COMPLETED = "flight_completed"
    STOCK_MOVED = "stock_moved"
    WO_SCHEDULED = "wo_scheduled"
    VENDOR_PRICE_CHANGED = "vendor_price_changed"
    PLAN_PUBLISHED = "plan_published"
    REMOVAL_RECORDED = "removal_recorded"
    EO_PUBLISHED = "eo_published"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Producer(_Frozen):
    system: str
    version: str
    instance: str


class FlightCompletedPayload(_Frozen):
    tail: str
    ac_type: str
    destination: str
    origin: str
    flight_hours: float = 0.0
    cycles: int = 0
    flight_date: date


class StockMovedPayload(_Frozen):
    pn: str
    sn: str | None = None
    from_location: str
    to_location: str
    from_condition: str
    to_condition: str
    qty: int
    transaction_type: str
    transaction_no: str
    wo: str | None = None
    moved_by: str | None = None


class WoScheduledPayload(_Frozen):
    wo: str
    tail: str | None = None
    ac_type: str | None = None
    location: str
    wo_type: str
    scheduled_start: datetime
    scheduled_end: datetime | None = None
    estimated_duration_days: float | None = None
    primary_eo: str | None = None


class VendorPriceChangedPayload(_Frozen):
    pn: str
    vendor: str
    condition: str
    old_price: float
    new_price: float
    currency: str
    old_lead_days: int
    new_lead_days: int
    preferred: bool = False
    effective_date: date


class PlanPublishedPayload(_Frozen):
    plan_id: str
    plan_type: str
    fleet: str
    horizon_days: int
    effective_from: date
    revision: int = 0


class RemovalRecordedPayload(_Frozen):
    pn: str
    sn: str | None = None
    tail: str
    ac_type: str | None = None
    location: str
    wo: str | None = None
    task_card: str | None = None
    removal_reason: str = ""  # UNTRUSTED free-text — scrub before LLM/observability
    schedule_category: str | None = None
    reason_category: str | None = None
    removed_at: datetime


class EoPublishedPayload(_Frozen):
    eo_number: str
    ata_chapter: str
    ata_subchapter: str | None = None
    affected_fleet: str
    affected_pn_pattern: str | None = None
    criticality: Literal["AD", "SB", "FLEET_CAMPAIGN", "OTHER"] = "OTHER"
    compliance_due: date | None = None
    compliance_threshold_hours: float | None = None
    compliance_threshold_cycles: int | None = None
    issued_by: str | None = None
    title: str = ""  # UNTRUSTED free-text — scrub before LLM/observability
    issued_at: datetime


Payload = Annotated[
    FlightCompletedPayload
    | StockMovedPayload
    | WoScheduledPayload
    | VendorPriceChangedPayload
    | PlanPublishedPayload
    | RemovalRecordedPayload
    | EoPublishedPayload,
    Field(union_mode="smart"),
]

_KIND_TO_TYPE: dict[EventKind, type] = {
    EventKind.FLIGHT_COMPLETED: FlightCompletedPayload,
    EventKind.STOCK_MOVED: StockMovedPayload,
    EventKind.WO_SCHEDULED: WoScheduledPayload,
    EventKind.VENDOR_PRICE_CHANGED: VendorPriceChangedPayload,
    EventKind.PLAN_PUBLISHED: PlanPublishedPayload,
    EventKind.REMOVAL_RECORDED: RemovalRecordedPayload,
    EventKind.EO_PUBLISHED: EoPublishedPayload,
}


class EventEnvelope(_Frozen):
    event_id: str
    tenant_id: str
    kind: EventKind
    occurred_at: datetime
    produced_at: datetime
    schema_version: str = "1.0.0"
    producer: Producer
    payload: Payload
    correlation_id: str | None = None
    causation_id: str | None = None

    @field_validator("event_id")
    @classmethod
    def _check_event_id(cls, v: str) -> str:
        if not is_uuid7(v):
            raise ValueError("event_id must be a UUIDv7")
        return v

    @field_validator("tenant_id")
    @classmethod
    def _check_tenant_id(cls, v: str) -> str:
        if not _KEBAB.match(v):
            raise ValueError("tenant_id must be kebab-case")
        return v

    @field_validator("schema_version")
    @classmethod
    def _check_semver(cls, v: str) -> str:
        if not _SEMVER.match(v):
            raise ValueError("schema_version must be semver MAJOR.MINOR.PATCH")
        return v

    @field_validator("occurred_at", "produced_at")
    @classmethod
    def _check_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware (UTC)")
        return v

    @model_validator(mode="after")
    def _check_kind_matches_payload(self) -> EventEnvelope:
        expected = _KIND_TO_TYPE[self.kind]
        if type(self.payload) is not expected:
            raise ValueError(
                f"payload type {type(self.payload).__name__} does not match kind {self.kind}"
            )
        return self
