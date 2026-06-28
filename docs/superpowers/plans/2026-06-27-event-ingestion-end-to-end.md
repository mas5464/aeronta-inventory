# End-to-End Event Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a consumer-side `EventIngestor` (dedup → adapt → handle → aggregate, invalid → dead-letter) and a `trax-io-spine ingest` CLI that replays a canonical-event JSONL through the agent-spine end to end.

**Architecture:** New module `trax_io_spine/event_lane/ingestor.py` (contracts + sink + ingestor). It wraps the existing `EventLaneHandler`, decodes canonical `EventEnvelope`s (from `trax_io_event_publisher`, already a dependency), down-projects via `canonical_adapter.to_domain_event`, and rolls up `OrchestrationResult.summary`s into an `IngestReport`. A `trax-io-spine ingest` typer command drives it over an extract dir's online bundle store.

**Tech Stack:** Python 3.14, pydantic v2, typer, uv + pytest + ruff. No new dependency.

## Global Constraints

- **Python ≥3.12, runs on 3.14.** Work entirely in `services/agent-spine`. Run tests with `cd services/agent-spine && uv run --extra dev pytest`; lint `uv run --extra dev ruff check .` (ruff line-length 100, select E/F/I/B/UP/N/SIM).
- **All new contract models** are pydantic v2 with `model_config = ConfigDict(frozen=True, extra="forbid")`.
- **One-way dependency preserved:** agent-spine imports from `trax_io_event_publisher` (canonical schema) — never the reverse. Do not touch the event-publisher package.
- **Do not modify** `EventLaneHandler`, the slim `DomainEvent` models, the canonical schema, or `canonical_adapter`.
- **`OrchestrationResult.summary` keys (exact):** `recommendations, written, deferred, failed, queued, rejected, skipped` (all ints).
- **`ingest` / `ingest_raw` never raise** — a malformed event is dead-lettered and returned as an `INVALID` outcome.
- **Classification rule:** `recompute["recommendations"] > 0` ⇒ `PROCESSED`, else `NO_OP`. (Verified: a `removal_recorded` at sample key `('FILTER-EXP-042','YYZ')` → `recommendations: 2`.)
- Imports available: `from trax_io_event_publisher import EventEnvelope, make_event`; `from trax_io_event_publisher.ids import new_event_id`; `from trax_io_feature_store import TenantContext`; `from trax_io_feature_store.materialize import materialize_bundle`; `from trax_io_reco.data.extract_loader import build_stores_from_extract`.
- Commit after each task with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Ingestion contracts + dead-letter sink

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/event_lane/ingestor.py`
- Test: `services/agent-spine/tests/event_lane/test_ingestor_contracts.py`

**Interfaces:**
- Produces:
  - `IngestStatus(StrEnum)`: `PROCESSED="processed", NO_OP="no_op", DUPLICATE="duplicate", INVALID="invalid"`.
  - `IngestOutcome(_Frozen)`: `status: IngestStatus`, `event_id: str | None`, `kind: str | None`, `recompute: dict[str, int] | None`, `reason: str | None = None`.
  - `IngestReport(_Frozen)`: `received, processed, no_op, duplicate, invalid: int`, `recompute_totals: dict[str, int]`, `outcomes: tuple[IngestOutcome, ...]`; classmethod `from_outcomes(outcomes: Sequence[IngestOutcome]) -> IngestReport`.
  - `DeadLetterSink(Protocol): def put(self, raw: str, reason: str) -> None: ...`
  - `InMemoryDeadLetterSink` with `.entries: list[tuple[str, str]]`.
  - Module constant `_SUMMARY_KEYS = ("recommendations","written","deferred","failed","queued","rejected","skipped")`.

- [ ] **Step 1: Write the failing test** — `tests/event_lane/test_ingestor_contracts.py`

```python
from trax_io_spine.event_lane.ingestor import (
    IngestOutcome,
    IngestReport,
    IngestStatus,
    InMemoryDeadLetterSink,
)


def _out(status, recompute=None, event_id="e", kind="stock_moved", reason=None):
    return IngestOutcome(
        status=status, event_id=event_id, kind=kind, recompute=recompute, reason=reason
    )


def test_from_outcomes_tallies_counts_and_sums_recompute():
    outcomes = [
        _out(IngestStatus.PROCESSED, {"recommendations": 2, "written": 1, "deferred": 0,
             "failed": 0, "queued": 1, "rejected": 0, "skipped": 0}),
        _out(IngestStatus.NO_OP, {"recommendations": 0, "written": 0, "deferred": 0,
             "failed": 0, "queued": 0, "rejected": 0, "skipped": 0}),
        _out(IngestStatus.DUPLICATE),
        _out(IngestStatus.INVALID, event_id=None, kind=None, reason="bad"),
    ]
    report = IngestReport.from_outcomes(outcomes)
    assert (report.received, report.processed, report.no_op, report.duplicate, report.invalid) \
        == (4, 1, 1, 1, 1)
    assert report.recompute_totals["recommendations"] == 2
    assert report.recompute_totals["written"] == 1
    assert report.recompute_totals["skipped"] == 0
    assert len(report.outcomes) == 4


def test_from_outcomes_empty():
    report = IngestReport.from_outcomes([])
    assert report.received == 0
    assert report.recompute_totals == {
        "recommendations": 0, "written": 0, "deferred": 0, "failed": 0,
        "queued": 0, "rejected": 0, "skipped": 0,
    }


def test_dead_letter_sink_records():
    sink = InMemoryDeadLetterSink()
    sink.put("{bad json", "1 validation error")
    assert sink.entries == [("{bad json", "1 validation error")]
```

- [ ] **Step 2: Run it, verify it fails** — `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_ingestor_contracts.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `ingestor.py`** (contracts + sink only; the `EventIngestor` class arrives in Task 2)

```python
"""Consumer-side event ingestion: canonical events -> adapt -> recompute -> report."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

_SUMMARY_KEYS = (
    "recommendations", "written", "deferred", "failed", "queued", "rejected", "skipped",
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IngestStatus(StrEnum):
    PROCESSED = "processed"
    NO_OP = "no_op"
    DUPLICATE = "duplicate"
    INVALID = "invalid"


class IngestOutcome(_Frozen):
    status: IngestStatus
    event_id: str | None
    kind: str | None
    recompute: dict[str, int] | None
    reason: str | None = None


class IngestReport(_Frozen):
    received: int
    processed: int
    no_op: int
    duplicate: int
    invalid: int
    recompute_totals: dict[str, int]
    outcomes: tuple[IngestOutcome, ...]

    @classmethod
    def from_outcomes(cls, outcomes: Sequence[IngestOutcome]) -> IngestReport:
        outcomes = tuple(outcomes)
        totals = dict.fromkeys(_SUMMARY_KEYS, 0)
        for o in outcomes:
            if o.recompute:
                for k in _SUMMARY_KEYS:
                    totals[k] += o.recompute.get(k, 0)
        counts = Counter(o.status for o in outcomes)
        return cls(
            received=len(outcomes),
            processed=counts[IngestStatus.PROCESSED],
            no_op=counts[IngestStatus.NO_OP],
            duplicate=counts[IngestStatus.DUPLICATE],
            invalid=counts[IngestStatus.INVALID],
            recompute_totals=totals,
            outcomes=outcomes,
        )


@runtime_checkable
class DeadLetterSink(Protocol):
    def put(self, raw: str, reason: str) -> None: ...


class InMemoryDeadLetterSink:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def put(self, raw: str, reason: str) -> None:
        self.entries.append((raw, reason))
```

- [ ] **Step 4: Run tests, verify pass + ruff clean.**

- [ ] **Step 5: Commit** — `git add services/agent-spine/src/trax_io_spine/event_lane/ingestor.py services/agent-spine/tests/event_lane/test_ingestor_contracts.py && git commit -m "#4 ingestor: ingestion contracts + dead-letter sink"`

---

### Task 2: `EventIngestor.ingest` (dedup + adapt + handle + classify)

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/event_lane/ingestor.py` (append `EventIngestor` with `ingest`)
- Create: `services/agent-spine/tests/event_lane/conftest.py` (shared `online_sample` fixture)
- Test: `services/agent-spine/tests/event_lane/test_ingestor.py`

**Interfaces:**
- Consumes: `EventLaneHandler`, `to_domain_event`, `OnlineStore`, `WritebackTarget`, `KeyResolver`, `EventEnvelope`.
- Produces: `EventIngestor(online_store, writeback, *, resolver=None, dlq=None, seen=None)` with `ingest(event: EventEnvelope) -> IngestOutcome`.

- [ ] **Step 1: Write the shared fixture** — `tests/event_lane/conftest.py`

```python
from pathlib import Path

import pytest
from trax_io_feature_store import TenantContext
from trax_io_feature_store.materialize import materialize_bundle
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.event_lane.online import InMemoryOnlineStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture
def online_sample():
    """(InMemoryOnlineStore, keys) materialized from #11's extract sample for tenant 'acme'."""
    fs, _inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    tenant = TenantContext(tenant_id=tid)
    bundles = [materialize_bundle(fs, tenant=tenant, pn=pn, location=loc) for pn, loc in keys]
    return InMemoryOnlineStore(bundles), keys
```

- [ ] **Step 2: Write the failing test** — `tests/event_lane/test_ingestor.py`

```python
from trax_io_event_publisher import make_event

from trax_io_spine.event_lane.ingestor import EventIngestor, IngestStatus
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _removal_for(pn, loc):
    base = make_event("removal_recorded", tenant_id="acme")
    return base.model_copy(
        update={"payload": base.payload.model_copy(update={"pn": pn, "location": loc})}
    )


def test_event_hitting_a_real_key_is_processed(online_sample):
    store, keys = online_sample
    pn, loc = keys[0]  # ('FILTER-EXP-042', 'YYZ') -> recommendations: 2
    ing = EventIngestor(store, InMemoryWritebackTarget())
    out = ing.ingest(_removal_for(pn, loc))
    assert out.status is IngestStatus.PROCESSED
    assert out.kind == "removal_recorded"
    assert out.recompute["recommendations"] > 0


def test_fan_out_kind_is_no_op(online_sample):
    store, _keys = online_sample
    ing = EventIngestor(store, InMemoryWritebackTarget())
    out = ing.ingest(make_event("flight_completed", tenant_id="acme"))
    assert out.status is IngestStatus.NO_OP
    assert out.recompute["recommendations"] == 0


def test_cross_tenant_event_is_no_op(online_sample):
    store, keys = online_sample
    pn, loc = keys[0]
    ing = EventIngestor(store, InMemoryWritebackTarget())
    other = _removal_for(pn, loc).model_copy(update={"tenant_id": "other-air"})
    assert ing.ingest(other).status is IngestStatus.NO_OP


def test_same_event_id_twice_is_duplicate(online_sample):
    store, keys = online_sample
    pn, loc = keys[0]
    ing = EventIngestor(store, InMemoryWritebackTarget())
    ev = _removal_for(pn, loc)
    assert ing.ingest(ev).status is IngestStatus.PROCESSED
    dup = ing.ingest(ev)
    assert dup.status is IngestStatus.DUPLICATE
    assert dup.recompute is None
```

- [ ] **Step 3: Run it, verify it fails.**

- [ ] **Step 4: Append `EventIngestor` to `ingestor.py`** (add imports at the top of the file alongside the existing ones)

```python
# add to the top-of-file imports:
from collections.abc import Iterable  # noqa: F401  (Iterable used in Task 3)

from trax_io_spine.event_lane.canonical_adapter import to_domain_event
from trax_io_spine.event_lane.handler import EventLaneHandler
from trax_io_spine.event_lane.keys import KeyResolver
from trax_io_spine.event_lane.online import OnlineStore
from trax_io_spine.writeback.target import WritebackTarget
from trax_io_event_publisher import EventEnvelope
```

```python
class EventIngestor:
    def __init__(
        self,
        online_store: OnlineStore,
        writeback: WritebackTarget,
        *,
        resolver: KeyResolver | None = None,
        dlq: DeadLetterSink | None = None,
        seen: set[str] | None = None,
    ) -> None:
        self._handler = EventLaneHandler(online_store, writeback, resolver=resolver)
        self._dlq = dlq or InMemoryDeadLetterSink()
        self._seen: set[str] = seen if seen is not None else set()

    def ingest(self, event: EventEnvelope) -> IngestOutcome:
        if event.event_id in self._seen:
            return IngestOutcome(
                status=IngestStatus.DUPLICATE, event_id=event.event_id,
                kind=event.kind.value, recompute=None,
            )
        self._seen.add(event.event_id)
        result = self._handler.handle(to_domain_event(event))
        summary = dict(result.summary)
        status = (
            IngestStatus.PROCESSED
            if summary.get("recommendations", 0) > 0
            else IngestStatus.NO_OP
        )
        return IngestOutcome(
            status=status, event_id=event.event_id, kind=event.kind.value, recompute=summary,
        )
```

Note: the `Iterable` import is unused until Task 3 — keep the `# noqa: F401` only if ruff flags it; otherwise add `Iterable` together with Task 3. If ruff complains in this task, move the `Iterable` import into Task 3 instead.

- [ ] **Step 5: Run tests, verify pass + ruff clean** — `uv run --extra dev pytest tests/event_lane/test_ingestor.py -v`.

- [ ] **Step 6: Commit** — `git commit -m "#4 ingestor: EventIngestor.ingest (dedup + adapt + handle + classify)"`

---

### Task 3: `ingest_raw` + `ingest_batch` + invalid → dead-letter

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/event_lane/ingestor.py` (add `ingest_raw`, `ingest_batch`)
- Test: `services/agent-spine/tests/event_lane/test_ingestor_batch.py`

**Interfaces:**
- Produces on `EventIngestor`:
  - `ingest_raw(raw: bytes | str) -> IngestOutcome` — validate via `EventEnvelope.model_validate_json`; on `ValidationError` → `dlq.put(raw_str, reason)` + return `INVALID` (event_id None, kind None, reason set); else delegate to `ingest`.
  - `ingest_batch(items: Iterable[bytes | str | EventEnvelope]) -> IngestReport` — `EventEnvelope` → `ingest`; `bytes`/`str` → `ingest_raw`; collect → `IngestReport.from_outcomes`.

- [ ] **Step 1: Write the failing test** — `tests/event_lane/test_ingestor_batch.py`

```python
from trax_io_event_publisher import make_event

from trax_io_spine.event_lane.ingestor import (
    EventIngestor,
    InMemoryDeadLetterSink,
    IngestStatus,
)
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _removal_for(pn, loc):
    base = make_event("removal_recorded", tenant_id="acme")
    return base.model_copy(
        update={"payload": base.payload.model_copy(update={"pn": pn, "location": loc})}
    )


def test_malformed_json_is_invalid_and_dead_lettered(online_sample):
    store, _keys = online_sample
    dlq = InMemoryDeadLetterSink()
    ing = EventIngestor(store, InMemoryWritebackTarget(), dlq=dlq)
    out = ing.ingest_raw(b"{not valid json")
    assert out.status is IngestStatus.INVALID
    assert out.event_id is None
    assert out.reason is not None
    assert len(dlq.entries) == 1


def test_schema_invalid_event_is_invalid(online_sample):
    store, _keys = online_sample
    dlq = InMemoryDeadLetterSink()
    ing = EventIngestor(store, InMemoryWritebackTarget(), dlq=dlq)
    out = ing.ingest_raw(b'{"kind": "stock_moved"}')  # missing envelope/payload fields
    assert out.status is IngestStatus.INVALID
    assert len(dlq.entries) == 1


def test_ingest_batch_tallies_mixed_stream(online_sample):
    store, keys = online_sample
    pn, loc = keys[0]
    ing = EventIngestor(store, InMemoryWritebackTarget())
    good = _removal_for(pn, loc)
    items = [
        good.model_dump_json(),                       # processed
        good.model_dump_json(),                       # duplicate (same event_id)
        make_event("flight_completed", tenant_id="acme"),  # no_op (EventEnvelope, not raw)
        b"{garbage",                                  # invalid
    ]
    report = ing.ingest_batch(items)
    assert report.received == 4
    assert report.processed == 1
    assert report.duplicate == 1
    assert report.no_op == 1
    assert report.invalid == 1
    assert report.recompute_totals["recommendations"] >= 2
```

- [ ] **Step 2: Run it, verify it fails.**

- [ ] **Step 3: Implement `ingest_raw` + `ingest_batch`** (add to `EventIngestor`; ensure `from collections.abc import Iterable` and `from pydantic import ValidationError` are imported)

```python
    def ingest_raw(self, raw: bytes | str) -> IngestOutcome:
        raw_str = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        try:
            event = EventEnvelope.model_validate_json(raw)
        except ValidationError as exc:
            reason = f"{exc.error_count()} validation error(s)"
            self._dlq.put(raw_str, reason)
            return IngestOutcome(
                status=IngestStatus.INVALID, event_id=None, kind=None,
                recompute=None, reason=reason,
            )
        return self.ingest(event)

    def ingest_batch(
        self, items: Iterable[bytes | str | EventEnvelope]
    ) -> IngestReport:
        outcomes = [
            self.ingest(item) if isinstance(item, EventEnvelope) else self.ingest_raw(item)
            for item in items
        ]
        return IngestReport.from_outcomes(outcomes)
```

Add `from pydantic import ValidationError` to the imports (the contracts already import `BaseModel, ConfigDict` from pydantic — extend that line or add a new import).

- [ ] **Step 4: Run tests, verify pass + ruff clean.**

- [ ] **Step 5: Commit** — `git commit -m "#4 ingestor: ingest_raw + ingest_batch + invalid->dead-letter"`

---

### Task 4: `trax-io-spine ingest` CLI command

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/cli.py` (add `ingest` command)
- Test: `services/agent-spine/tests/test_cli_ingest.py`

**Interfaces:**
- Consumes: `EventIngestor`, `InMemoryOnlineStore`, `materialize_bundle`, `build_stores_from_extract`, `InMemoryWritebackTarget`, `RestWritebackClient`.
- Produces: a typer command `ingest` on the existing `app`, printing `json.dumps(report.model_dump(exclude={"outcomes"}))`.

- [ ] **Step 1: Write the failing test** — `tests/test_cli_ingest.py`

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from trax_io_event_publisher import make_event

from trax_io_spine.cli import app

runner = CliRunner()
_SAMPLE = (
    Path(__file__).resolve().parents[1] / "recommendation-engine" / "examples" / "extract_sample"
)


def _removal_for(pn, loc):
    base = make_event("removal_recorded", tenant_id="acme")
    return base.model_copy(
        update={"payload": base.payload.model_copy(update={"pn": pn, "location": loc})}
    )


def test_ingest_prints_a_report(tmp_path):
    events = tmp_path / "events.jsonl"
    ev = _removal_for("FILTER-EXP-042", "YYZ")
    events.write_text(
        ev.model_dump_json() + "\n"
        + make_event("flight_completed", tenant_id="acme").model_dump_json() + "\n"
    )
    result = runner.invoke(
        app,
        ["ingest", "--extract-dir", str(_SAMPLE), "--tenant", "acme",
         "--events", str(events), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["received"] == 2
    assert report["processed"] == 1
    assert report["no_op"] == 1
    assert "outcomes" not in report
    assert report["recompute_totals"]["recommendations"] >= 2


def test_ingest_skips_blank_lines(tmp_path):
    events = tmp_path / "events.jsonl"
    ev = _removal_for("FILTER-EXP-042", "YYZ")
    events.write_text("\n" + ev.model_dump_json() + "\n\n")
    result = runner.invoke(
        app,
        ["ingest", "--extract-dir", str(_SAMPLE), "--tenant", "acme",
         "--events", str(events), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["received"] == 1
```

- [ ] **Step 2: Run it, verify it fails** — `uv run --extra dev pytest tests/test_cli_ingest.py -v`.

- [ ] **Step 3: Add the `ingest` command to `cli.py`** (after the `run` command; reuse the existing module imports `json`, `TenantContext`, `build_stores_from_extract`, `RestWritebackClient`, `InMemoryWritebackTarget`)

```python
@app.command(name="ingest")
def ingest(
    extract_dir: str = typer.Option(..., "--extract-dir"),
    tenant: str = typer.Option(..., "--tenant"),
    events: str = typer.Option(..., "--events", help="JSONL of canonical events"),
    apply: bool = typer.Option(False, "--apply/--dry-run"),
    writeback_url: str = typer.Option("http://localhost:9000", "--writeback-url"),
) -> None:
    from pathlib import Path  # noqa: PLC0415

    from trax_io_feature_store.materialize import materialize_bundle  # noqa: PLC0415

    from trax_io_spine.event_lane.ingestor import EventIngestor  # noqa: PLC0415
    from trax_io_spine.event_lane.online import InMemoryOnlineStore  # noqa: PLC0415

    fs, _inv, tenant_id, keys = build_stores_from_extract(extract_dir, tenant_id=tenant)
    tctx = TenantContext(tenant_id=tenant_id)
    bundles = [materialize_bundle(fs, tenant=tctx, pn=pn, location=loc) for pn, loc in keys]
    target = RestWritebackClient(writeback_url) if apply else InMemoryWritebackTarget()
    ingestor = EventIngestor(InMemoryOnlineStore(bundles), target)
    lines = [ln for ln in Path(events).read_text().splitlines() if ln.strip()]
    report = ingestor.ingest_batch(lines)
    typer.echo(json.dumps(report.model_dump(exclude={"outcomes"})))
```

- [ ] **Step 4: Run tests, verify pass + ruff clean.** Confirm `uv run trax-io-spine ingest --help` lists the command.

- [ ] **Step 5: Commit** — `git commit -m "#4 cli: trax-io-spine ingest (JSONL replay -> IngestReport)"`

---

### Task 5: End-to-end integration test (producer oracle → ingestor)

**Files:**
- Test: `services/agent-spine/tests/event_lane/test_ingestion_integration.py`

**Interfaces:** Consumes `EventIngestor`, `trax_io_event_publisher.make_event` + `EventEnvelope`, the `online_sample` fixture (from Task 2's conftest).

- [ ] **Step 1: Write the test** — `tests/event_lane/test_ingestion_integration.py`

```python
from trax_io_event_publisher import EventEnvelope, make_event

from trax_io_spine.event_lane.ingestor import EventIngestor, InMemoryDeadLetterSink
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _removal_for(pn, loc):
    base = make_event("removal_recorded", tenant_id="acme")
    return base.model_copy(
        update={"payload": base.payload.model_copy(update={"pn": pn, "location": loc})}
    )


def test_producer_oracle_events_drive_recompute_end_to_end(online_sample):
    # Build a JSONL feed the way the eMRO producer would emit it, using #3's oracle.
    store, keys = online_sample
    pn, loc = keys[0]
    good = _removal_for(pn, loc)
    feed = [
        good.model_dump_json(),                              # PROCESSED
        good.model_dump_json(),                              # DUPLICATE (same event_id)
        make_event("eo_published", tenant_id="acme").model_dump_json(),  # NO_OP (fan-out)
        "{ truncated",                                       # INVALID
    ]
    dlq = InMemoryDeadLetterSink()
    ing = EventIngestor(store, InMemoryWritebackTarget(), dlq=dlq)
    report = ing.ingest_batch(feed)

    assert (report.received, report.processed, report.duplicate, report.no_op, report.invalid) \
        == (4, 1, 1, 1, 1)
    assert report.recompute_totals["recommendations"] >= 2
    assert len(dlq.entries) == 1
    # every parsed line that adapted is one of our canonical events
    assert all(
        o.event_id is not None
        for o in report.outcomes
        if o.status.value != "invalid"
    )


def test_each_oracle_kind_round_trips_through_ingestor(online_sample):
    store, _keys = online_sample
    ing = EventIngestor(store, InMemoryWritebackTarget())
    for kind in ["flight_completed", "stock_moved", "wo_scheduled", "vendor_price_changed",
                 "plan_published", "removal_recorded", "eo_published"]:
        raw = make_event(kind, tenant_id="acme").model_dump_json()
        out = ing.ingest_raw(raw)
        assert out.status.value in {"processed", "no_op"}  # never invalid/duplicate here
        assert EventEnvelope.model_validate_json(raw).kind.value == kind
```

- [ ] **Step 2: Run it, verify it passes** — `uv run --extra dev pytest tests/event_lane/test_ingestion_integration.py -v`.

- [ ] **Step 3: Run the full agent-spine suite** to confirm no regression — `uv run --extra dev pytest -q` (expect prior 48 + the new ingestor tests). ruff clean.

- [ ] **Step 4: Commit** — `git commit -m "#4 ingestor: end-to-end integration (producer oracle -> ingestor -> report)"`

---

## Post-implementation (controller, after final review)

- ADR `docs/adr/2026-06-27-0008-consumer-side-event-ingestor.md` (consumer-side ingestion seam in agent-spine; one-way-dep rationale; idempotency-via-event_id; HTTP/AWS deferred).
- CLAUDE.md: note the `trax-io-spine ingest` CLI alongside `run`.
- ROADMAP #4: add the event ingestion entry; note HTTP ingestion service + Step Functions/EventBridge/Kinesis deferred.
- TASKS.md session entry. Merge `feat/event-ingestion` → main, push, delete branch.

## Self-Review

- **Spec coverage:** §3.1 contracts → Task 1; §3.2 sink → Task 1; §3.3 ingestor (`ingest`/`ingest_raw`/`ingest_batch`) → Tasks 2–3; §3.4 CLI → Task 4; §5 integration → Task 5. All covered.
- **Type consistency:** `IngestStatus`, `IngestOutcome`, `IngestReport.from_outcomes`, `EventIngestor(online_store, writeback, *, resolver, dlq, seen)`, `_SUMMARY_KEYS` consistent across tasks. CLI uses `model_dump(exclude={"outcomes"})` matching the `IngestReport` field name.
- **Placeholders:** none — every step has runnable code. The only soft spot flagged inline is the `Iterable` import ordering between Tasks 2 and 3 (move it to Task 3 if ruff flags an unused import in Task 2).
