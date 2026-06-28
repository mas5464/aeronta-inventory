# #3 eMRO Event Publisher — Contract-Test Harness (slice A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a locally-verifiable Python package that expresses the eMRO outbound-event wire contract as executable code, plus a `fake_emro` producer + reference endpoint test harness, and an adapter so the shipped event lane ingests real contract events.

**Architecture:** New peer service `services/event-publisher/` (`trax_io_event_publisher`) holds the canonical full-fidelity schema (single source of truth), a `Transport`-seam producer with retry/backoff/DLQ, and a FastAPI reference endpoint. `agent-spine` gains a one-way path dependency on it plus a `to_domain_event` adapter that down-projects canonical → the slim `DomainEvent` it already consumes. Java triggers/CDC + real mTLS/AWS stay deferred behind the `Transport`/`DeadLetterQueue` stubs.

**Tech Stack:** Python 3.14, pydantic v2, FastAPI + httpx (proven on this interpreter), stdlib `uuid.uuid7()`, uv + pytest + ruff.

## Global Constraints

- **Python ≥3.12, runs on 3.14.** Package `name = "trax-io-event-publisher"`, python package `trax_io_event_publisher`, hatchling build, `packages = ["src/trax_io_event_publisher"]`.
- **uv + pytest + ruff.** `[tool.pytest.ini_options] pythonpath = ["src"]`. ruff `line-length = 100`, `target-version = "py312"`, `select = ["E","F","I","B","UP","N","SIM"]`.
- **All schema/contract models** are pydantic v2 with `model_config = ConfigDict(frozen=True, extra="forbid")`.
- **The 7 event kinds (snake_case, exact):** `flight_completed, stock_moved, wo_scheduled, vendor_price_changed, plan_published, removal_recorded, eo_published`.
- **Canonical schema is the single source of truth.** The slim event-lane `DomainEvent` is never edited; reconciliation is one-way via the adapter (Task 8).
- **Dependency direction is one-way:** `trax-io-event-publisher` depends on nothing in-repo; `agent-spine` depends on it.
- **`EventPublisher.publish` and the endpoint never crash the caller** — they return a result / HTTP status, they do not propagate transport errors to callers.
- **Untrusted free-text fields:** `removal_recorded.removal_reason`, `eo_published.title`. Exported in `UNTRUSTED_FIELDS`.
- Commit after each task with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Package scaffold + UUIDv7 ids + Schemathesis probe

**Files:**
- Create: `services/event-publisher/pyproject.toml`
- Create: `services/event-publisher/src/trax_io_event_publisher/__init__.py`
- Create: `services/event-publisher/src/trax_io_event_publisher/ids.py`
- Test: `services/event-publisher/tests/test_ids.py`

**Interfaces:**
- Produces: `ids.new_event_id() -> str` (a UUIDv7 string); `ids.is_uuid7(value: str) -> bool` (True iff a well-formed UUID with version == 7).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "trax-io-event-publisher"
version = "0.1.0"
description = "Trax IO eMRO outbound-event wire contract + fake_emro test harness"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.6.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.6"]
http = ["fastapi>=0.115", "httpx>=0.27"]
schemathesis = ["schemathesis>=3.0"]

[project.scripts]
trax-io-publisher = "trax_io_event_publisher.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/trax_io_event_publisher"]

[tool.pytest.ini_options]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "SIM"]
```

Note: the `[project.scripts]` entry references `cli.py` which arrives in Task 7. That is fine — the entry point is only resolved when invoked. If `uv sync` complains before Task 7, leave the scripts table out and add it in Task 7.

- [ ] **Step 2: Write the failing test** — `tests/test_ids.py`

```python
import uuid

from trax_io_event_publisher.ids import is_uuid7, new_event_id


def test_new_event_id_is_uuid7():
    eid = new_event_id()
    assert is_uuid7(eid)
    assert uuid.UUID(eid).version == 7


def test_new_event_ids_are_unique():
    assert new_event_id() != new_event_id()


def test_is_uuid7_rejects_v4_and_garbage():
    assert is_uuid7(str(uuid.uuid4())) is False
    assert is_uuid7("not-a-uuid") is False
    assert is_uuid7("") is False
```

- [ ] **Step 3: Run it, verify it fails** — `cd services/event-publisher && uv run --extra dev pytest tests/test_ids.py -v` → FAIL (module missing).

- [ ] **Step 4: Implement `ids.py`**

```python
"""UUIDv7 event-id helpers (stdlib uuid.uuid7, available on Python 3.14)."""

from __future__ import annotations

import uuid


def new_event_id() -> str:
    return str(uuid.uuid7())


def is_uuid7(value: str) -> bool:
    try:
        return uuid.UUID(value).version == 7
    except (ValueError, AttributeError, TypeError):
        return False
```

Leave `__init__.py` empty for now (public exports are added as later tasks land).

- [ ] **Step 5: Run tests, verify pass** — `uv run --extra dev pytest tests/test_ids.py -v` → PASS. Then `uv run --extra dev ruff check .` → clean.

- [ ] **Step 6: Probe Schemathesis installability (record only)** — run `uv sync --extra schemathesis` and record in the task report whether it resolves on Python 3.14. If it fails, note that Task 9 will be dropped to a ROADMAP follow-up. Do **not** add schemathesis to any required path.

- [ ] **Step 7: Commit**

```bash
git add services/event-publisher/pyproject.toml services/event-publisher/src services/event-publisher/tests
git commit -m "#3 event-publisher: package scaffold + UUIDv7 ids"
```

---

### Task 2: Canonical wire-contract schema

**Files:**
- Create: `services/event-publisher/src/trax_io_event_publisher/schemas.py`
- Test: `services/event-publisher/tests/test_schemas.py`

**Interfaces:**
- Consumes: `ids.is_uuid7`.
- Produces:
  - `EventKind(StrEnum)` with the 7 members (value == snake_case string).
  - Payload models: `FlightCompletedPayload, StockMovedPayload, WoScheduledPayload, VendorPriceChangedPayload, PlanPublishedPayload, RemovalRecordedPayload, EoPublishedPayload`.
  - `Producer` `{system: str, version: str, instance: str}`.
  - `EventEnvelope` (fields per code below). `payload` is the smart union of the 7 payloads; an after-validator asserts the payload type matches `kind`.
  - `UNTRUSTED_FIELDS: frozenset[str]` = `{"removal_recorded.removal_reason", "eo_published.title"}`.
  - `scrub(text: str) -> str` — baseline neutralization (strip ASCII control chars, collapse whitespace, cap 500 chars).
  - `schema_version_compatible(consumer_major: int, event_version: str) -> bool` — True iff `event_version` is valid semver and its major == `consumer_major`.

- [ ] **Step 1: Write the failing test** — `tests/test_schemas.py`

```python
from datetime import UTC, date, datetime

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
            transaction_type="TRANSFER", transaction_no="T1", wo="WO1", moved_by="op1",
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
    assert UNTRUSTED_FIELDS == frozenset(
        {"removal_recorded.removal_reason", "eo_published.title"}
    )


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
```

- [ ] **Step 2: Run it, verify it fails** — `uv run --extra dev pytest tests/test_schemas.py -v` → FAIL.

- [ ] **Step 3: Implement `schemas.py`**

```python
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
```

- [ ] **Step 4: Run tests, verify pass** — `uv run --extra dev pytest tests/test_schemas.py -v` → PASS. `uv run --extra dev ruff check .` → clean.

- [ ] **Step 5: Commit**

```bash
git add services/event-publisher/src/trax_io_event_publisher/schemas.py services/event-publisher/tests/test_schemas.py
git commit -m "#3 event-publisher: canonical wire-contract schema (envelope + 7 rich payloads)"
```

---

### Task 3: Sample factory (test oracle)

**Files:**
- Create: `services/event-publisher/src/trax_io_event_publisher/samples.py`
- Test: `services/event-publisher/tests/test_samples.py`

**Interfaces:**
- Consumes: all of `schemas`, `ids.new_event_id`.
- Produces: `samples.make_event(kind: EventKind | str, **overrides) -> EventEnvelope` — a valid-by-construction envelope for any of the 7 kinds. `overrides` apply to envelope-level fields (e.g. `tenant_id`, `event_id`, `correlation_id`); `payload=<Payload>` may be passed to override the whole payload.

- [ ] **Step 1: Write the failing test** — `tests/test_samples.py`

```python
import pytest

from trax_io_event_publisher.samples import make_event
from trax_io_event_publisher.schemas import EventKind


@pytest.mark.parametrize("kind", list(EventKind))
def test_make_event_is_valid_for_every_kind(kind):
    env = make_event(kind)
    assert env.kind == kind
    assert type(env.payload) is not None
    # round-trips through JSON without error
    type(env).model_validate_json(env.model_dump_json())


def test_make_event_accepts_string_kind():
    env = make_event("stock_moved")
    assert env.kind == EventKind.STOCK_MOVED


def test_overrides_apply():
    env = make_event(EventKind.STOCK_MOVED, tenant_id="other-air")
    assert env.tenant_id == "other-air"
```

- [ ] **Step 2: Run it, verify it fails** — `uv run --extra dev pytest tests/test_samples.py -v` → FAIL.

- [ ] **Step 3: Implement `samples.py`** — build one valid payload per kind, then a default envelope, applying overrides. Use fixed timestamps (no clock dependence).

```python
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
        transaction_type="TRANSFER", transaction_no="T1", wo="WO1", moved_by="op1",
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
```

- [ ] **Step 4: Run tests, verify pass + ruff clean.**

- [ ] **Step 5: Commit** — `git commit -m "#3 event-publisher: sample factory (valid-by-construction oracle for 7 kinds)"`

---

### Task 4: Transport seam (Protocol + FakeTransport + deferred stub)

**Files:**
- Create: `services/event-publisher/src/trax_io_event_publisher/transport.py`
- Test: `services/event-publisher/tests/test_transport.py`

**Interfaces:**
- Produces:
  - `TransportResponse(_Frozen)`: `status_code: int`, `retry_after_s: float | None = None`, `body: dict | None = None`.
  - `class TransportError(Exception)` — raised by real transports on connection failure; treated as retryable by the producer.
  - `class Transport(Protocol): def send(self, *, tenant_id: str, body: bytes) -> TransportResponse: ...`
  - `FakeTransport(responses=None, *, default=202)`: `responses` is an iterable of `TransportResponse | TransportError | int` consumed one per `send`; once exhausted, returns `TransportResponse(default)`. Records every call in `.sent: list[tuple[str, bytes]]`. An item that is a `TransportError` is raised.
  - `HttpsMtlsTransport` — `send` raises `NotImplementedError("Phase 2: real mTLS + AWS transport")`.
- `AsgiTransport` is added in Task 6 (needs the endpoint app).

- [ ] **Step 1: Write the failing test** — `tests/test_transport.py`

```python
import pytest

from trax_io_event_publisher.transport import (
    FakeTransport,
    HttpsMtlsTransport,
    TransportError,
    TransportResponse,
)


def test_fake_transport_records_and_returns_default_202():
    t = FakeTransport()
    resp = t.send(tenant_id="acme-air", body=b"{}")
    assert resp.status_code == 202
    assert t.sent == [("acme-air", b"{}")]


def test_fake_transport_scripts_responses_in_order():
    t = FakeTransport([500, TransportResponse(status_code=202)])
    assert t.send(tenant_id="a", body=b"x").status_code == 500
    assert t.send(tenant_id="a", body=b"x").status_code == 202


def test_fake_transport_can_raise_transport_error():
    t = FakeTransport([TransportError("conn reset")])
    with pytest.raises(TransportError):
        t.send(tenant_id="a", body=b"x")


def test_https_mtls_transport_is_deferred():
    with pytest.raises(NotImplementedError):
        HttpsMtlsTransport().send(tenant_id="a", body=b"x")
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement `transport.py`**

```python
"""Producer transport seam. FakeTransport for tests; real mTLS/AWS deferred."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class TransportError(Exception):
    """Connection-level failure; retryable by the producer."""


class TransportResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status_code: int
    retry_after_s: float | None = None
    body: dict | None = None


@runtime_checkable
class Transport(Protocol):
    def send(self, *, tenant_id: str, body: bytes) -> TransportResponse: ...


def _coerce(item: object) -> TransportResponse:
    if isinstance(item, TransportResponse):
        return item
    if isinstance(item, int):
        return TransportResponse(status_code=item)
    raise TypeError(f"unsupported scripted response: {item!r}")


class FakeTransport:
    def __init__(
        self, responses: Iterable[object] | None = None, *, default: int = 202
    ) -> None:
        self._queue = list(responses or [])
        self._default = default
        self.sent: list[tuple[str, bytes]] = []

    def send(self, *, tenant_id: str, body: bytes) -> TransportResponse:
        self.sent.append((tenant_id, body))
        if not self._queue:
            return TransportResponse(status_code=self._default)
        item = self._queue.pop(0)
        if isinstance(item, TransportError):
            raise item
        return _coerce(item)


class HttpsMtlsTransport:
    def send(self, *, tenant_id: str, body: bytes) -> TransportResponse:
        raise NotImplementedError("Phase 2: real mTLS + AWS transport")
```

- [ ] **Step 4: Run tests, verify pass + ruff clean.**

- [ ] **Step 5: Commit** — `git commit -m "#3 event-publisher: transport seam (Protocol + FakeTransport + deferred mTLS stub)"`

---

### Task 5: Producer — retry / backoff / dead-letter

**Files:**
- Create: `services/event-publisher/src/trax_io_event_publisher/dlq.py`
- Create: `services/event-publisher/src/trax_io_event_publisher/publisher.py`
- Test: `services/event-publisher/tests/test_publisher.py`

**Interfaces:**
- Consumes: `schemas.EventEnvelope`, `transport.{Transport, TransportResponse, TransportError}`, `samples.make_event` (tests).
- Produces:
  - `dlq.DeadLetterQueue(Protocol): def put(self, event: EventEnvelope, reason: str) -> None: ...`
  - `dlq.InMemoryDeadLetterQueue` with `.entries: list[tuple[EventEnvelope, str]]`.
  - `dlq.S3DeadLetterQueue` — `put` raises `NotImplementedError("Phase 2: S3 dead-letter")`.
  - `publisher.PublishStatus(StrEnum)`: `EMITTED, REJECTED, DEAD_LETTERED`.
  - `publisher.PublishResult(_Frozen)`: `status: PublishStatus`, `attempts: int`, `last_status_code: int | None`, `dead_lettered: bool`.
  - `publisher.EventPublisher(transport, *, dlq=None, max_attempts=7, backoff_s=(1,2,4,8,16,32,60), sleep=time.sleep)` with `publish(event: EventEnvelope) -> PublishResult`.

**Behavior (contract response-code table):** 202/409 → `EMITTED`. 400/401/403 → terminal, no retry → DLQ, `REJECTED`. 429 → sleep `retry_after_s` (else `backoff_s[attempt-1]`), retry. 5xx or `TransportError` → sleep `backoff_s[attempt-1]`, retry. On exhausting `max_attempts` while still failing → DLQ, `DEAD_LETTERED`. `attempts` counts transport sends. `sleep` is injected (tests record calls, assert schedule).

- [ ] **Step 1: Write the failing test** — `tests/test_publisher.py`

```python
from trax_io_event_publisher.dlq import InMemoryDeadLetterQueue
from trax_io_event_publisher.publisher import EventPublisher, PublishStatus
from trax_io_event_publisher.samples import make_event
from trax_io_event_publisher.transport import FakeTransport, TransportError, TransportResponse


def _recording_sleep():
    waits: list[float] = []
    return waits, waits.append


def test_202_is_emitted_first_try():
    pub = EventPublisher(FakeTransport([202]))
    res = pub.publish(make_event("stock_moved"))
    assert res.status is PublishStatus.EMITTED
    assert res.attempts == 1


def test_409_duplicate_is_idempotent_success():
    res = EventPublisher(FakeTransport([409])).publish(make_event("stock_moved"))
    assert res.status is PublishStatus.EMITTED


def test_400_is_terminal_no_retry_dead_letters():
    dlq = InMemoryDeadLetterQueue()
    t = FakeTransport([400])
    res = EventPublisher(t, dlq=dlq).publish(make_event("stock_moved"))
    assert res.status is PublishStatus.REJECTED
    assert res.attempts == 1
    assert len(t.sent) == 1
    assert len(dlq.entries) == 1


def test_5xx_retries_with_backoff_then_dead_letters():
    waits, sleep = _recording_sleep()
    dlq = InMemoryDeadLetterQueue()
    t = FakeTransport([500, 500, 500], default=500)  # always 5xx
    res = EventPublisher(
        t, dlq=dlq, max_attempts=3, backoff_s=(1, 2, 4), sleep=sleep
    ).publish(make_event("stock_moved"))
    assert res.status is PublishStatus.DEAD_LETTERED
    assert res.attempts == 3
    assert waits == [1, 2]  # slept before retries 2 and 3, not after the last
    assert len(dlq.entries) == 1


def test_transport_error_is_retryable_then_succeeds():
    waits, sleep = _recording_sleep()
    t = FakeTransport([TransportError("reset"), TransportResponse(status_code=202)])
    res = EventPublisher(t, max_attempts=3, backoff_s=(1, 2, 4), sleep=sleep).publish(
        make_event("stock_moved")
    )
    assert res.status is PublishStatus.EMITTED
    assert res.attempts == 2
    assert waits == [1]


def test_429_honors_retry_after_then_succeeds():
    waits, sleep = _recording_sleep()
    t = FakeTransport(
        [TransportResponse(status_code=429, retry_after_s=9.0), TransportResponse(status_code=202)]
    )
    res = EventPublisher(t, sleep=sleep).publish(make_event("stock_moved"))
    assert res.status is PublishStatus.EMITTED
    assert waits == [9.0]
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement `dlq.py`**

```python
"""Dead-letter sinks. In-memory for tests; S3 deferred to Phase 2."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from trax_io_event_publisher.schemas import EventEnvelope


@runtime_checkable
class DeadLetterQueue(Protocol):
    def put(self, event: EventEnvelope, reason: str) -> None: ...


class InMemoryDeadLetterQueue:
    def __init__(self) -> None:
        self.entries: list[tuple[EventEnvelope, str]] = []

    def put(self, event: EventEnvelope, reason: str) -> None:
        self.entries.append((event, reason))


class S3DeadLetterQueue:
    def put(self, event: EventEnvelope, reason: str) -> None:
        raise NotImplementedError("Phase 2: S3 dead-letter")
```

- [ ] **Step 4: Implement `publisher.py`**

```python
"""eMRO-side producer: at-least-once delivery with retry/backoff/dead-letter."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from trax_io_event_publisher.dlq import DeadLetterQueue, InMemoryDeadLetterQueue
from trax_io_event_publisher.schemas import EventEnvelope
from trax_io_event_publisher.transport import Transport, TransportError

_TERMINAL = {400, 401, 403}
_SUCCESS = {202, 409}


class PublishStatus(StrEnum):
    EMITTED = "emitted"
    REJECTED = "rejected"
    DEAD_LETTERED = "dead_lettered"


class PublishResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: PublishStatus
    attempts: int
    last_status_code: int | None
    dead_lettered: bool


class EventPublisher:
    def __init__(
        self,
        transport: Transport,
        *,
        dlq: DeadLetterQueue | None = None,
        max_attempts: int = 7,
        backoff_s: Sequence[float] = (1, 2, 4, 8, 16, 32, 60),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._dlq = dlq or InMemoryDeadLetterQueue()
        self._max_attempts = max_attempts
        self._backoff = tuple(backoff_s)
        self._sleep = sleep

    def publish(self, event: EventEnvelope) -> PublishResult:
        body = event.model_dump_json().encode("utf-8")
        last_code: int | None = None
        for attempt in range(1, self._max_attempts + 1):
            retry_after: float | None = None
            try:
                resp = self._transport.send(tenant_id=event.tenant_id, body=body)
                last_code = resp.status_code
                if resp.status_code in _SUCCESS:
                    return self._result(PublishStatus.EMITTED, attempt, last_code, False)
                if resp.status_code in _TERMINAL:
                    self._dlq.put(event, f"terminal {resp.status_code}")
                    return self._result(PublishStatus.REJECTED, attempt, last_code, True)
                if resp.status_code == 429:
                    retry_after = resp.retry_after_s
                # else: 5xx -> retryable
            except TransportError:
                last_code = None  # connection failure, no HTTP code
            if attempt < self._max_attempts:
                wait = retry_after if retry_after is not None else self._backoff_for(attempt)
                self._sleep(wait)
        self._dlq.put(event, f"exhausted {self._max_attempts} attempts")
        return self._result(PublishStatus.DEAD_LETTERED, self._max_attempts, last_code, True)

    def _backoff_for(self, attempt: int) -> float:
        idx = min(attempt - 1, len(self._backoff) - 1)
        return self._backoff[idx]

    @staticmethod
    def _result(
        status: PublishStatus, attempts: int, last_code: int | None, dead: bool
    ) -> PublishResult:
        return PublishResult(
            status=status, attempts=attempts, last_status_code=last_code, dead_lettered=dead
        )
```

- [ ] **Step 5: Run tests, verify pass + ruff clean.** Confirm the `waits == [1, 2]` schedule assertion passes (sleep happens before retries, not after the final attempt).

- [ ] **Step 6: Commit** — `git commit -m "#3 event-publisher: producer with retry/backoff/dead-letter (contract response-code table)"`

---

### Task 6: Fake reference endpoint + ASGI transport + round-trip

**Files:**
- Create: `services/event-publisher/src/trax_io_event_publisher/endpoint.py`
- Modify: `services/event-publisher/src/trax_io_event_publisher/transport.py` (add `AsgiTransport`)
- Test: `services/event-publisher/tests/test_endpoint.py`

**Interfaces:**
- Consumes: `schemas.EventEnvelope`, `transport`.
- Produces:
  - `endpoint.create_app(*, rate_limiter=None) -> fastapi.FastAPI`. `rate_limiter` is an optional `Callable[[str, EventEnvelope], bool]` returning True to reject with 429. App keeps an in-memory tenant-scoped accepted-store; expose it via `app.state.accepted: dict[tuple[str, str], EventEnvelope]`.
  - Routes: `POST /v1/tenants/{tenant_id}/events` → 202 / 400 / 403 / 409 / 429; `POST /v1/tenants/{tenant_id}/events/replay` → returns stored events for the tenant.
  - `transport.AsgiTransport(app, *, base_url="http://emro.test")` implementing `Transport.send` via `httpx.Client(transport=httpx.ASGITransport(app=app))`, POSTing to the per-tenant path and mapping the HTTP response (and `Retry-After`) into a `TransportResponse`; httpx connection errors become `TransportError`.

- [ ] **Step 1: Write the failing test** — `tests/test_endpoint.py` (uses `fastapi.testclient.TestClient`; mark the file to require the `http` extra — if FastAPI import fails, that is a setup error, not a test failure).

```python
from fastapi.testclient import TestClient

from trax_io_event_publisher.endpoint import create_app
from trax_io_event_publisher.publisher import EventPublisher, PublishStatus
from trax_io_event_publisher.samples import make_event
from trax_io_event_publisher.transport import AsgiTransport


def _client(**kw):
    app = create_app(**kw)
    return app, TestClient(app)


def _post(client, env):
    return client.post(
        f"/v1/tenants/{env.tenant_id}/events",
        content=env.model_dump_json(),
        headers={"content-type": "application/json"},
    )


def test_happy_path_returns_202_and_stores():
    app, client = _client()
    env = make_event("stock_moved")
    assert _post(client, env).status_code == 202
    assert (env.tenant_id, env.event_id) in app.state.accepted


def test_bad_schema_returns_400():
    _, client = _client()
    r = client.post(
        "/v1/tenants/acme-air/events", content=b'{"not":"valid"}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400


def test_tenant_mismatch_returns_403():
    _, client = _client()
    env = make_event("stock_moved", tenant_id="acme-air")
    r = client.post(
        "/v1/tenants/other-air/events", content=env.model_dump_json(),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 403


def test_duplicate_event_id_returns_409():
    _, client = _client()
    env = make_event("stock_moved")
    assert _post(client, env).status_code == 202
    assert _post(client, env).status_code == 409


def test_rate_limiter_returns_429_with_retry_after():
    _, client = _client(rate_limiter=lambda tenant, env: True)
    r = _post(client, make_event("stock_moved"))
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}


def test_replay_returns_stored_events():
    _, client = _client()
    env = make_event("stock_moved")
    _post(client, env)
    r = client.post(f"/v1/tenants/{env.tenant_id}/events/replay")
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_asgi_transport_round_trip_with_publisher():
    app = create_app()
    pub = EventPublisher(AsgiTransport(app))
    env = make_event("stock_moved")
    assert pub.publish(env).status is PublishStatus.EMITTED
    # re-publish -> endpoint 409 -> still idempotent success
    assert pub.publish(env).status is PublishStatus.EMITTED
```

- [ ] **Step 2: Run it, verify it fails** — `uv run --extra dev --extra http pytest tests/test_endpoint.py -v` → FAIL.

- [ ] **Step 3: Implement `endpoint.py`**

```python
"""Trax IO reference event endpoint (fake_event_endpoint) — contract response codes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from trax_io_event_publisher.schemas import EventEnvelope

RateLimiter = Callable[[str, EventEnvelope], bool]


def create_app(*, rate_limiter: RateLimiter | None = None) -> FastAPI:
    app = FastAPI(title="fake_event_endpoint")
    app.state.accepted = {}

    @app.post("/v1/tenants/{tenant_id}/events")
    async def ingest(tenant_id: str, request: Request) -> Response:
        raw = await request.body()
        try:
            env = EventEnvelope.model_validate_json(raw)
        except ValidationError as exc:
            return JSONResponse(status_code=400, content={"error": exc.errors(include_url=False)})
        if env.tenant_id != tenant_id:
            return JSONResponse(status_code=403, content={"error": "tenant mismatch"})
        if rate_limiter is not None and rate_limiter(tenant_id, env):
            return JSONResponse(
                status_code=429, content={"error": "rate limited"},
                headers={"Retry-After": "1"},
            )
        key = (tenant_id, env.event_id)
        if key in app.state.accepted:
            return JSONResponse(status_code=409, content={"error": "duplicate event_id"})
        app.state.accepted[key] = env
        return JSONResponse(status_code=202, content={"status": "accepted"})

    @app.post("/v1/tenants/{tenant_id}/events/replay")
    async def replay(tenant_id: str) -> Response:
        events = [
            e.model_dump(mode="json")
            for (tid, _), e in app.state.accepted.items()
            if tid == tenant_id
        ]
        return JSONResponse(status_code=200, content={"count": len(events), "events": events})

    return app
```

- [ ] **Step 4: Add `AsgiTransport` to `transport.py`** (append; keep imports local so the base `transport` module has no hard FastAPI/httpx dependency)

```python
class AsgiTransport:
    """Real in-process HTTP round-trip to a FastAPI app (no sockets/mTLS)."""

    def __init__(self, app: object, *, base_url: str = "http://emro.test") -> None:
        import httpx

        self._client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url=base_url)

    def send(self, *, tenant_id: str, body: bytes) -> TransportResponse:
        import httpx

        try:
            resp = self._client.post(
                f"/v1/tenants/{tenant_id}/events",
                content=body,
                headers={"content-type": "application/json"},
            )
        except httpx.TransportError as exc:  # connection-level failure
            raise TransportError(str(exc)) from exc
        retry_after = resp.headers.get("retry-after")
        return TransportResponse(
            status_code=resp.status_code,
            retry_after_s=float(retry_after) if retry_after is not None else None,
        )
```

- [ ] **Step 5: Run tests, verify pass** — `uv run --extra dev --extra http pytest tests/test_endpoint.py -v` → PASS. Then run the whole suite `uv run --extra dev --extra http pytest -q` and `uv run --extra dev ruff check .` → clean.

- [ ] **Step 6: Commit** — `git commit -m "#3 event-publisher: fake reference endpoint + ASGI transport round-trip"`

---

### Task 7: CLI (`trax-io-publisher emit`)

**Files:**
- Create: `services/event-publisher/src/trax_io_event_publisher/cli.py`
- Test: `services/event-publisher/tests/test_cli.py`
- (If the `[project.scripts]` table was deferred in Task 1, add it now.)

**Interfaces:**
- Produces: `cli.main(argv: list[str] | None = None) -> int`. Subcommand `emit --kind <kind> --tenant <tenant-id> [--to stdout|fake]`. `stdout` (default) prints the canonical event JSON and returns 0. `fake` POSTs it through `AsgiTransport(create_app())` via `EventPublisher` and prints the `PublishResult`. Use stdlib `argparse` (no new dependency).

- [ ] **Step 1: Write the failing test** — `tests/test_cli.py`

```python
import json

from trax_io_event_publisher.cli import main


def test_emit_to_stdout_prints_valid_event(capsys):
    rc = main(["emit", "--kind", "stock_moved", "--tenant", "acme-air"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "stock_moved"
    assert payload["tenant_id"] == "acme-air"


def test_emit_to_fake_reports_emitted(capsys):
    rc = main(["emit", "--kind", "removal_recorded", "--tenant", "acme-air", "--to", "fake"])
    assert rc == 0
    assert "emitted" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement `cli.py`** (the `fake` path imports `endpoint`/`AsgiTransport` lazily so `--to stdout` works without the `http` extra)

```python
"""trax-io-publisher CLI — emit canonical events (stdout) or through the fake endpoint."""

from __future__ import annotations

import argparse

from trax_io_event_publisher.samples import make_event
from trax_io_event_publisher.schemas import EventKind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trax-io-publisher")
    sub = parser.add_subparsers(dest="cmd", required=True)
    emit = sub.add_parser("emit", help="emit a sample canonical event")
    emit.add_argument("--kind", required=True, choices=[k.value for k in EventKind])
    emit.add_argument("--tenant", required=True)
    emit.add_argument("--to", default="stdout", choices=["stdout", "fake"])
    args = parser.parse_args(argv)

    event = make_event(args.kind, tenant_id=args.tenant)
    if args.to == "stdout":
        print(event.model_dump_json(indent=2))
        return 0

    from trax_io_event_publisher.endpoint import create_app
    from trax_io_event_publisher.publisher import EventPublisher
    from trax_io_event_publisher.transport import AsgiTransport

    result = EventPublisher(AsgiTransport(create_app())).publish(event)
    print(f"{result.status.value} (attempts={result.attempts})")
    return 0
```

- [ ] **Step 4: Run tests, verify pass + ruff clean.** Verify `uv run trax-io-publisher emit --kind stock_moved --tenant acme-air` prints JSON.

- [ ] **Step 5: Populate `__init__.py` public exports** — re-export the stable surface so consumers import from the package root:

```python
from trax_io_event_publisher.schemas import (
    UNTRUSTED_FIELDS,
    EventEnvelope,
    EventKind,
    Producer,
    schema_version_compatible,
    scrub,
)
from trax_io_event_publisher.samples import make_event

__all__ = [
    "UNTRUSTED_FIELDS", "EventEnvelope", "EventKind", "Producer",
    "schema_version_compatible", "scrub", "make_event",
]
```

- [ ] **Step 6: Commit** — `git commit -m "#3 event-publisher: CLI emit + package public exports"`

---

### Task 8: Consumer adapter in agent-spine (down-project canonical → slim DomainEvent)

**Files:**
- Modify: `services/agent-spine/pyproject.toml` (add `trax-io-event-publisher` dep + path source)
- Create: `services/agent-spine/src/trax_io_spine/event_lane/canonical_adapter.py`
- Test: `services/agent-spine/tests/event_lane/test_canonical_adapter.py`

**Interfaces:**
- Consumes: `trax_io_event_publisher.schemas.EventEnvelope` + the 7 payload types; the **existing** `trax_io_spine.event_lane.events.DomainEvent` and its slim payloads (read them first to get exact field names — do not edit them).
- Produces: `canonical_adapter.to_domain_event(canonical: EventEnvelope) -> DomainEvent`, mapping the envelope (`tenant_id`, `event_id`, `occurred_at`, `schema_version`, `kind`) and selecting each slim payload's fields from the rich canonical payload.

**Context:** This is the one-way reconciliation. The slim payload field sets are a strict subset of the canonical fields for every kind. **Read `services/agent-spine/src/trax_io_spine/event_lane/events.py` first** and map each slim payload field to its canonical source. Do not add fields to the slim models.

- [ ] **Step 1: Add the dependency** — in `services/agent-spine/pyproject.toml`, add `"trax-io-event-publisher"` to `dependencies`, and under `[tool.uv.sources]` add `trax-io-event-publisher = { path = "../event-publisher" }` (non-editable, matching the existing `trax-io-feature-store`/`trax-io-reco` entries). Run `uv sync` (or `uv sync --reinstall-package trax-io-event-publisher`).

- [ ] **Step 2: Write the failing test** — `tests/event_lane/test_canonical_adapter.py`

```python
from trax_io_event_publisher.samples import make_event

from trax_io_spine.event_lane.canonical_adapter import to_domain_event
from trax_io_spine.event_lane.events import EventKind as SlimKind


def test_stock_moved_down_projects():
    canonical = make_event("stock_moved", tenant_id="acme-air")
    slim = to_domain_event(canonical)
    assert slim.tenant_id == "acme-air"
    assert slim.kind == SlimKind.STOCK_MOVED
    assert slim.payload.pn == canonical.payload.pn
    assert slim.payload.from_location == canonical.payload.from_location
    assert slim.payload.to_location == canonical.payload.to_location
    assert slim.payload.qty == canonical.payload.qty
    assert slim.occurred_at == canonical.occurred_at


def test_every_kind_adapts_without_error():
    for kind in ["flight_completed", "stock_moved", "wo_scheduled",
                 "vendor_price_changed", "plan_published", "removal_recorded", "eo_published"]:
        slim = to_domain_event(make_event(kind))
        assert slim.kind.value == kind
```

- [ ] **Step 3: Run it, verify it fails.**

- [ ] **Step 4: Implement `canonical_adapter.py`** — write one `_to_<kind>_payload` mapping per kind, then a dispatch keyed by `EventKind`. The exact slim payload constructors/field names come from `events.py` (read it in Step 0); the mapping is field selection from the canonical payload. The envelope maps: `tenant_id`, `event_id`, `occurred_at`, `schema_version`, and `kind` (translate the canonical `EventKind` to the slim `EventKind` by value). Keep the function total over all 7 kinds; raise `ValueError` on an unknown kind (defensive, unreachable).

- [ ] **Step 5: Run tests, verify pass** — `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_canonical_adapter.py -v` → PASS. Then run the full event-lane suite to confirm no regression: `uv run --extra dev pytest tests/event_lane -q`. ruff clean.

- [ ] **Step 6: Commit** — `git commit -m "#3 agent-spine: canonical->slim event adapter (consumer reconciliation)"`

---

### Task 9 (conditional): Schemathesis property tests

**Only if Task 1's probe showed schemathesis installs on Python 3.14.** If it did not, skip this task and add a ROADMAP follow-up line; the hand-written contract tests are the gate.

**Files:**
- Create: `services/event-publisher/tests/test_schemathesis.py`

**Interfaces:** Consumes `endpoint.create_app` (its FastAPI OpenAPI schema) + `samples`.

- [ ] **Step 1: Write the property test** — load the app's OpenAPI schema with schemathesis and assert the endpoint accepts every `make_event` kind (positive conformance) and returns 400 (not 500) on malformed bodies. Gate the import: `pytest.importorskip("schemathesis")`.

```python
import pytest

schemathesis = pytest.importorskip("schemathesis")

from fastapi.testclient import TestClient  # noqa: E402

from trax_io_event_publisher.endpoint import create_app  # noqa: E402
from trax_io_event_publisher.samples import make_event  # noqa: E402
from trax_io_event_publisher.schemas import EventKind  # noqa: E402


@pytest.mark.parametrize("kind", [k.value for k in EventKind])
def test_endpoint_accepts_every_valid_kind(kind):
    client = TestClient(create_app())
    env = make_event(kind)
    r = client.post(
        f"/v1/tenants/{env.tenant_id}/events",
        content=env.model_dump_json(),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 202


def test_malformed_body_never_500s():
    client = TestClient(create_app())
    for bad in [b"", b"{}", b'{"kind":"stock_moved"}', b"not json"]:
        r = client.post(
            "/v1/tenants/acme-air/events", content=bad,
            headers={"content-type": "application/json"},
        )
        assert r.status_code in (400, 422)
```

- [ ] **Step 2: Run** `uv run --extra dev --extra http --extra schemathesis pytest tests/test_schemathesis.py -v` → PASS.

- [ ] **Step 3: Commit** — `git commit -m "#3 event-publisher: schemathesis/property conformance tests"`

---

## Post-implementation (controller, after final review)

- ADR `docs/adr/2026-06-27-0007-event-publisher-canonical-contract-harness.md` (canonical-schema-source-of-truth + harness slice + Java/AWS deferrals).
- CLAUDE.md Section A: add the `services/event-publisher` run/test row (note `--extra http` for endpoint tests, `--extra schemathesis` if it installed). Note the new agent-spine path dependency.
- ROADMAP #3: mark slice A done; list Java triggers/CDC + mTLS + EventBridge/Kinesis/S3-audit + operator UI as deferred.
- TASKS.md session entry. Merge `feat/event-publisher-harness` → main, push, delete branch (finishing-a-development-branch Option 1).

## Self-Review

- **Spec coverage:** §4 schema → Task 2; §5 transport → Tasks 4/6; §6 producer → Task 5; §7 endpoint → Task 6; §8 adapter → Task 8; §9 testing → Tasks 2–9; samples/CLI → Tasks 3/7. All covered.
- **Type consistency:** `EventEnvelope`, `EventKind`, `TransportResponse`, `PublishResult/PublishStatus`, `to_domain_event` names match across tasks. `FakeTransport` scripting (`int | TransportResponse | TransportError`) is consistent between Tasks 4 and 5. `sleep` is injected before-retry only (the `waits == [1, 2]` assertion encodes the exact semantics the impl must honor).
- **Placeholders:** none — every code step has runnable code; Task 8's per-field mapping is the one deliberate "read the existing slim model first" step because those field names live in shipped code the implementer must not guess.
