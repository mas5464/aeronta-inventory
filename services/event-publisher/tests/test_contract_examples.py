"""Wire-contract fidelity guard.

The canonical schema is the single source of truth for the eMRO outbound-event
wire contract. These are the exact payload examples published in
docs/contracts/2026-04-14-emro-event-publisher-contract.md — verbatim. If a future
edit to the schema stops parsing the contract's own examples (as `transaction_no: str`
once did against the integer `88412`), that is the drift this harness exists to catch.
"""

from datetime import UTC, datetime

import pytest

from trax_io_event_publisher.ids import new_event_id
from trax_io_event_publisher.schemas import EventEnvelope, EventKind, Producer

# Payloads copied verbatim from the contract's JSON examples (one per kind).
_CONTRACT_PAYLOADS: dict[str, dict] = {
    "flight_completed": {
        "tail": "C-FABC", "ac_type": "A320", "destination": "YYZ", "origin": "LHR",
        "flight_hours": 7.42, "cycles": 1, "flight_date": "2026-04-14",
    },
    "stock_moved": {
        "pn": "NSN-12345", "sn": "SN-9876543", "from_location": "YYZ-MAIN",
        "to_location": "YYZ-LINE-A1", "from_condition": "NEW", "to_condition": "RESERVED",
        "qty": 1, "transaction_type": "RESERVATION", "transaction_no": 88412,
        "wo": "WO-2026-04-1042", "moved_by": "user-yyz-mech-42",
    },
    "wo_scheduled": {
        "wo": "WO-2026-04-1042", "tail": "C-FABC", "ac_type": "A320", "location": "YYZ-MAIN",
        "wo_type": "C-CHECK", "scheduled_start": "2026-05-01T08:00:00Z",
        "scheduled_end": "2026-05-08T18:00:00Z", "estimated_duration_days": 7,
        "primary_eo": "EO-2026-0301",
    },
    "vendor_price_changed": {
        "pn": "NSN-12345", "vendor": "VEND-LH", "condition": "NEW", "old_price": 4500.00,
        "new_price": 5100.00, "currency": "USD", "old_lead_days": 14, "new_lead_days": 21,
        "preferred": True, "effective_date": "2026-05-01",
    },
    "plan_published": {
        "plan_id": "PLAN-2026-Q3-AC", "plan_type": "MAINTENANCE_PROGRAM", "fleet": "A320",
        "horizon_days": 180, "effective_from": "2026-07-01", "revision": 3,
    },
    "removal_recorded": {
        "pn": "LRU-CFM56-HPT-BLADE", "sn": "SN-9876543", "tail": "C-FABC", "ac_type": "A320",
        "location": "YYZ-MAIN", "wo": "WO-2026-04-1042", "task_card": "TC-04-12-BLADE-INSP",
        "removal_reason": "Engine borescope finding — leading edge erosion",
        "schedule_category": "UN/SCHEDULE", "reason_category": "WEAR",
        "removed_at": "2026-04-14T17:42:18Z",
    },
    "eo_published": {
        "eo_number": "EO-2026-0401", "ata_chapter": "32", "ata_subchapter": "32-41",
        "affected_fleet": "A320", "affected_pn_pattern": "WHEEL-A320-MLG-*", "criticality": "AD",
        "compliance_due": "2026-09-30", "compliance_threshold_hours": 1500,
        "compliance_threshold_cycles": 1000, "issued_by": "TC-CIVIL-AVIATION",
        "issued_at": "2026-04-14T00:00:00Z",
        "title": "Mandatory inspection and replacement of A320 MLG wheel assembly...",
    },
}

_PRODUCER = Producer(system="emro", version="2026.4.0", instance="yyz-1")


@pytest.mark.parametrize("kind,payload", list(_CONTRACT_PAYLOADS.items()))
def test_canonical_schema_parses_every_contract_example(kind: str, payload: dict):
    env = EventEnvelope(
        event_id=new_event_id(),
        tenant_id="acme-air",
        kind=EventKind(kind),
        occurred_at=datetime(2026, 4, 14, tzinfo=UTC),
        produced_at=datetime(2026, 4, 14, tzinfo=UTC),
        producer=_PRODUCER,
        payload=payload,
    )
    assert env.kind.value == kind
    # the payload smart-union resolved to a typed model carrying the example's values
    assert env.payload.model_dump(mode="json").keys() >= {k for k in payload}


def test_all_seven_kinds_covered():
    assert set(_CONTRACT_PAYLOADS) == {k.value for k in EventKind}
