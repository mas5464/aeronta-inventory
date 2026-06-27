from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trax_io_spine.event_lane.events import (
    DomainEvent,
    EventKind,
    RemovalRecordedPayload,
    StockMovedPayload,
)


def test_event_kind_has_the_seven_design_kinds() -> None:
    assert {k.value for k in EventKind} == {
        "flight_completed", "stock_moved", "wo_scheduled", "vendor_price_changed",
        "plan_published", "removal_recorded", "eo_published",
    }


def test_stock_moved_event_round_trips_json() -> None:
    ev = DomainEvent(
        tenant_id="acme", kind=EventKind.STOCK_MOVED, occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload=StockMovedPayload(pn="PN-A", from_location="LOC-1", to_location="LOC-2", qty=3),
    )
    assert DomainEvent.model_validate_json(ev.model_dump_json()) == ev
    assert ev.schema_version == "1.0.0"


def test_payload_is_frozen_and_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        RemovalRecordedPayload(pn="PN-A", tail="C-FABC", location="LOC-1", bogus=1)
