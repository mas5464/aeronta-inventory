from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trax_io_event_publisher.ids import new_event_id
from trax_io_event_publisher.schemas import (
    UNTRUSTED_FIELDS,
    EventEnvelope,
    EventKind,
    Producer,
    StockMovedPayload,
    schema_version_compatible,
    scrub,
)

_PRODUCER = Producer(system="emro", version="2026.4", instance="lhr-1")


def _envelope(**over):
    base = dict(
        event_id=new_event_id(),
        tenant_id="acme-air",
        kind=EventKind.STOCK_MOVED,
        occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        produced_at=datetime(2026, 4, 1, tzinfo=UTC),
        producer=_PRODUCER,
        payload=StockMovedPayload(
            pn="A320-WHEEL", sn="SN1", from_location="JFK", to_location="LHR",
            from_condition="SVC", to_condition="SVC", qty=1,
            transaction_type="TRANSFER", transaction_no=88412, wo="WO1", moved_by="op1",
        ),
    )
    base.update(over)
    return EventEnvelope(**base)


def test_stock_moved_round_trips_json():
    env = _envelope()
    again = EventEnvelope.model_validate_json(env.model_dump_json())
    assert again == env
    assert again.kind == EventKind.STOCK_MOVED


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        _envelope(unexpected="x")


def test_kind_payload_mismatch_rejected():
    with pytest.raises(ValidationError):
        _envelope(kind=EventKind.FLIGHT_COMPLETED)  # payload is stock_moved


def test_bad_event_id_rejected():
    with pytest.raises(ValidationError):
        _envelope(event_id="not-a-uuid7")


def test_bad_tenant_id_rejected():
    with pytest.raises(ValidationError):
        _envelope(tenant_id="Acme_Air")  # not kebab-case


def test_bad_semver_rejected():
    with pytest.raises(ValidationError):
        _envelope(schema_version="1.0")


def test_schema_version_defaults_to_1_0_0():
    assert _envelope().schema_version == "1.0.0"


def test_untrusted_fields_exported():
    assert frozenset({"removal_recorded.removal_reason", "eo_published.title"}) == UNTRUSTED_FIELDS


def test_scrub_strips_control_chars_and_caps():
    dirty = "drop\x00 table\n\n   users" + "x" * 600
    cleaned = scrub(dirty)
    assert "\x00" not in cleaned and "\n" not in cleaned
    assert len(cleaned) <= 500


@pytest.mark.parametrize(
    "major,version,ok",
    [(1, "1.4.2", True), (1, "2.0.0", False), (2, "2.1.0", True), (1, "x", False)],
)
def test_schema_version_compatible(major, version, ok):
    assert schema_version_compatible(major, version) is ok


def test_all_seven_kinds_present():
    assert {k.value for k in EventKind} == {
        "flight_completed", "stock_moved", "wo_scheduled", "vendor_price_changed",
        "plan_published", "removal_recorded", "eo_published",
    }
