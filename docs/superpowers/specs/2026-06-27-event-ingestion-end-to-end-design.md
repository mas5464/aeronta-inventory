# End-to-End Event Ingestion (agent-spine `EventIngestor` + `ingest` CLI) — Design

**Date:** 2026-06-27
**Status:** Proposed
**Sub-project:** #4 Agent Spine / #3 consumer integration (event-driven recompute loop)
**Authoritative inputs:**
[design §event-triggered recompute](../../design/2026-04-14-trax-io-inventory-optimizer-design.md) ·
[event-publisher contract](../../contracts/2026-04-14-emro-event-publisher-contract.md) ·
[ADR-0007 canonical contract harness](../../adr/2026-06-27-0007-event-publisher-canonical-contract-harness.md)

## 1. Context

After #3 slice A, every piece of the real-time recompute loop exists but nothing wires them together end to end:

| Component | Role |
|---|---|
| `trax_io_event_publisher.EventEnvelope` / `make_event` | canonical events + producer oracle |
| `event_lane.canonical_adapter.to_domain_event` | canonical → slim `DomainEvent` |
| `event_lane.handler.EventLaneHandler.handle(DomainEvent) → OrchestrationResult` | resolve keys → online bundle → recompute → writeback |
| `event_lane.online.InMemoryOnlineStore` | per-(pn, location) feature bundles |
| `trax-io-spine` CLI (typer `app`) | offline orchestration entrypoint |

This slice adds the **connector**: a consumer-side `EventIngestor` that takes a stream of canonical events and drives them through adapter → handler → aggregated result, plus a `trax-io-spine ingest` CLI that replays a JSONL feed end to end.

**Hard architectural constraint (grounded):** the event-publisher's `fake_event_endpoint` **cannot** drive the spine — that inverts the one-way dependency (event-publisher depends on nothing in-repo; agent-spine depends on it) and creates a cycle. Consumer-side ingestion therefore lives in **agent-spine**, importing the canonical schema it already depends on.

This realizes the design's **event-triggered recompute** path (Events → recompute of `(ROP, EOQ, SS, Max)`) and the contract's **at-least-once delivery with idempotency via `event_id`** — on the consumer side.

## 2. Scope

**In scope (locally verifiable):**
1. `EventIngestor` — dedup by `event_id`, adapt, handle, classify outcome; invalid raw → dead-letter.
2. Ingestion contracts — `IngestStatus`, `IngestOutcome`, `IngestReport` (frozen pydantic).
3. A consumer-side `DeadLetterSink` (`InMemoryDeadLetterSink`) for unparseable/invalid events.
4. `trax-io-spine ingest` CLI — build online store from an extract dir, replay a canonical-event JSONL, print the `IngestReport`.
5. An integration test tying event-publisher `make_event` → JSONL → ingestor end to end.

**Deferred (tracked in ROADMAP):** a real HTTP ingestion service (new agent-spine FastAPI endpoint); Step Functions / EventBridge / per-tenant Kinesis transport; hot-parts scheduling/cadence; the production `KeyResolver` catalog/BOM fan-out (separate work — fan-out kinds remain `NO_OP` today).

**Non-goals:** changing `EventLaneHandler`, the slim `DomainEvent` models, or the canonical schema; async/concurrent ingestion.

## 3. Components

New module `services/agent-spine/src/trax_io_spine/event_lane/ingestor.py` (contracts + sink + ingestor — one cohesive unit).

### 3.1 Contracts

```python
class IngestStatus(StrEnum):
    PROCESSED = "processed"   # adapted + handled, recompute produced >=1 recommendation
    NO_OP = "no_op"           # handled but resolved no keys/bundles (or 0 recommendations)
    DUPLICATE = "duplicate"   # event_id already seen (idempotency)
    INVALID = "invalid"       # raw did not validate against the canonical schema -> dead-lettered

class IngestOutcome(_Base):   # frozen, extra=forbid
    status: IngestStatus
    event_id: str | None      # None when INVALID (unparseable)
    kind: str | None
    recompute: dict[str, int] | None   # OrchestrationResult.summary for PROCESSED/NO_OP; else None
    reason: str | None = None          # set for INVALID (validation error summary)

class IngestReport(_Base):    # frozen
    received: int
    processed: int
    no_op: int
    duplicate: int
    invalid: int
    recompute_totals: dict[str, int]   # summed OrchestrationResult.summary across handled events
    outcomes: tuple[IngestOutcome, ...]

    @classmethod
    def from_outcomes(cls, outcomes: Sequence[IngestOutcome]) -> "IngestReport": ...
```

`recompute_totals` sums the seven `OrchestrationResult.summary` keys (`recommendations, written, deferred, failed, queued, rejected, skipped`) over every PROCESSED/NO_OP outcome.

### 3.2 Dead-letter sink

```python
class DeadLetterSink(Protocol):
    def put(self, raw: str, reason: str) -> None: ...

class InMemoryDeadLetterSink:
    entries: list[tuple[str, str]]    # (raw, reason) for assertions
```

(Consumer-side, raw-string based — distinct from the producer-side `trax_io_event_publisher.dlq.DeadLetterQueue`, which dead-letters typed `EventEnvelope`s. The deferred S3 sink slots in behind this Protocol.)

### 3.3 EventIngestor

```python
class EventIngestor:
    def __init__(self, online_store, writeback, *, resolver=None,
                 dlq: DeadLetterSink | None = None, seen: set[str] | None = None):
        self._handler = EventLaneHandler(online_store, writeback, resolver=resolver)
        self._dlq = dlq or InMemoryDeadLetterSink()
        self._seen: set[str] = seen if seen is not None else set()

    def ingest(self, event: EventEnvelope) -> IngestOutcome: ...
    def ingest_raw(self, raw: bytes | str) -> IngestOutcome: ...
    def ingest_batch(self, items: Iterable[bytes | str | EventEnvelope]) -> IngestReport: ...
```

- **`ingest(event)`**: if `event.event_id` in `seen` → `DUPLICATE` (no recompute). Else add to `seen`, `to_domain_event(event)`, `handler.handle(...)`. Classify `PROCESSED` if `summary["recommendations"] > 0` else `NO_OP`. `recompute = summary`.
- **`ingest_raw(raw)`**: `EventEnvelope.model_validate_json(raw)`; on `ValidationError` → `dlq.put(raw_str, reason)` and return `INVALID` (event_id None); else delegate to `ingest`.
- **`ingest_batch(items)`**: route each item (`EventEnvelope` → `ingest`; `bytes`/`str` → `ingest_raw`); collect outcomes → `IngestReport.from_outcomes`.

`ingest` and `ingest_raw` never raise — a malformed event is dead-lettered, mirroring the producer/endpoint contract that ingestion failures are recorded, not propagated.

### 3.4 CLI: `trax-io-spine ingest`

```
trax-io-spine ingest --extract-dir <dir> --tenant <id> --events <file.jsonl>
                     [--apply | --dry-run] [--now <ISO>]
```

Builds `fs, inv, tenant_id, keys` via `build_stores_from_extract`; materializes bundles (`materialize_bundle` per key) into `InMemoryOnlineStore`; writeback is `RestWritebackClient(url)` under `--apply` else `InMemoryWritebackTarget`; reads the JSONL line-by-line into `ingest_batch`; `typer.echo(json.dumps(report.model_dump(exclude={"outcomes"})))` (counts + recompute_totals; per-event outcomes omitted from the summary line).

## 4. Data flow

```
events.jsonl ─▶ EventIngestor.ingest_batch
                  │  per line
                  ├─ validate (canonical schema) ──fail──▶ INVALID ▶ DeadLetterSink
                  ├─ dedup by event_id ───────────seen──▶ DUPLICATE
                  └─ to_domain_event ─▶ EventLaneHandler.handle
                                          ├─ no keys/bundles / 0 recs ─▶ NO_OP
                                          └─ recompute + writeback ────▶ PROCESSED
                  ▼
              IngestReport (counts + summed recompute_totals)
```

## 5. Testing strategy

- **Contracts/report** — `IngestReport.from_outcomes` aggregates counts + sums `recompute_totals` across a mixed outcome list; `InMemoryDeadLetterSink.put` records.
- **Ingestor unit** (over the #11 extract sample's online store):
  - `PROCESSED` — a `removal_recorded`/`stock_moved` targeting a real `(pn, location)` recomputes (recommendations routed).
  - `NO_OP` — a `flight_completed` (fan-out → no keys) and a cross-tenant event both classify `NO_OP`.
  - `DUPLICATE` — same `event_id` twice → second is `DUPLICATE`, no second recompute.
  - `INVALID` — malformed JSON / schema-invalid raw → `INVALID`, dead-lettered, no raise.
  - `ingest_batch` over a mixed list → correct `IngestReport` tallies.
- **CLI** (typer `CliRunner`) — `ingest` over the extract sample + a temp JSONL prints a report with the expected counts; `--dry-run` writes nothing.
- **Integration** — event-publisher `make_event` builds canonical events (incl. payload overrides onto a real key) → serialized JSONL → `ingest_batch`; asserts the producer-oracle events drive PROCESSED/NO_OP/DUPLICATE/INVALID end to end. This ties #3's producer side to the spine consumer.

## 6. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Ingestor classification ambiguity (PROCESSED vs NO_OP) | Single rule: `recommendations > 0` ⇒ PROCESSED, documented + tested at the boundary. |
| Dedup `seen` set grows unbounded for long runs | Acceptable for v1 batch replay; a bounded/persistent store is a deferred follow-up (noted in ROADMAP). |
| CLI online-store build differs from `run`'s store | `ingest` deliberately uses the **online** bundle store (event lane), built via `materialize_bundle`, mirroring the existing `event_lane` integration test — not `run`'s direct fs/inv path. |
| One-way dep accidentally inverted | Ingestion lives entirely in agent-spine; it imports the canonical schema (allowed), never the reverse. |

## 7. Deliverables

- `event_lane/ingestor.py` (contracts + sink + `EventIngestor`) with full unit + integration tests.
- `trax-io-spine ingest` CLI subcommand + CLI test.
- ADR-0008 (consumer-side ingestion seam; HTTP/AWS deferred; idempotency-via-event_id).
- CLAUDE.md `ingest` CLI note; ROADMAP #4 entry; TASKS.md.
