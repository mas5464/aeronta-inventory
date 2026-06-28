# #3 eMRO Outbound Event Publisher — Contract-Test Harness (slice A) — Design

**Date:** 2026-06-27
**Status:** Proposed
**Sub-project:** #3 eMRO Outbound Event Publisher (P1)
**Authoritative inputs:**
[contract](../../contracts/2026-04-14-emro-event-publisher-contract.md) ·
[plan](../../plans/2026-04-14-event-publisher-plan.md) ·
[design §ingestion](../../design/2026-04-14-trax-io-inventory-optimizer-design.md) ·
[ADR-0003 fake_emro contract testing](../../adr/0003-fake-emro-contract-testing.md)

## 1. Context

#3's real body is **Java inside eMRO** (Oracle triggers / CDC → an outbound drainer with mTLS) plus **AWS transport** (mTLS endpoint → EventBridge → per-tenant Kinesis → Iceberg CDC + Step Functions recompute). None of that is buildable or locally verifiable in this Python monorepo, and the legacy Java reference (`~/ptcwebservice/`) is batch file-export with no outbound eventing — i.e. greenfield.

What **is** locally buildable — and what the contract, the plan, and ADR-0003 all explicitly name as the Trax-side, contract-first deliverable — is a **canonical wire-contract expressed as executable code, plus a fake-eMRO producer/endpoint test harness**. This is the same slicing used for #1 (extract utility) and #6 (writeback `fake_emro`): lock the integration contract as machine-checkable truth that both the future eMRO Java producer and our already-shipped consumer validate against, so the eventual Java build is de-risked and the wire contract can never silently drift.

### The tension this slice resolves

The shipped event lane (`services/agent-spine/src/trax_io_spine/event_lane/`, built earlier this session) consumes **slim** `DomainEvent` models — e.g. `StockMovedPayload{pn, from_location, to_location, qty}`. The Apr-14 contract is **richer** — `stock_moved` carries `{pn, sn, from_location, to_location, from_condition, to_condition, qty, transaction_type, transaction_no, wo, moved_by}`, and the envelope adds `event_id` (UUIDv7), `produced_at`, `producer{system,version,instance}`, `correlation_id`, `causation_id`. Two sources of truth exist today. **Decision (approved):** the new package holds the full-fidelity canonical schema as the single source of truth; the shipped event lane keeps its slim models and gains an **adapter that down-projects** a canonical event to the slim `DomainEvent` it already consumes — no breaking change to shipped, tested code.

## 2. Scope

**In scope (slice A — locally verifiable):**
1. Canonical event schema (full contract fidelity) as frozen Pydantic v2 — the wire contract as code.
2. `fake_emro` event **producer** with a `Transport` seam, producer-side retry / exponential backoff / dead-letter.
3. `fake_event_endpoint` — a FastAPI reference of `POST /v1/tenants/{tenant_id}/events` (+ `/replay`) enforcing the contract's response-code table and `event_id` idempotency.
4. An in-process HTTP transport (httpx ASGI) wiring producer ↔ endpoint for a real round-trip without sockets/mTLS.
5. Shared contract tests + (best-effort) Schemathesis property tests proving producer/endpoint conformance, semver compatibility, idempotency, response-code handling.
6. A `to_domain_event` **consumer adapter** in the event lane, with a round-trip test (canonical event → adapter → slim `DomainEvent` → `EventLaneHandler` → `OrchestrationResult`).
7. Sample/factory generators (the test oracle) + an optional CLI to emit valid events.

**Deferred (Java/AWS — tracked in ROADMAP, behind the same seams):** Oracle triggers / CDC; the Spring Boot drainer; real mTLS client + AWS Private CA cert issuance; EventBridge / per-tenant Kinesis / S3 audit bucket; operator UI; per-kind feature flags in eMRO. These sit behind `Transport` (the `HttpsMtlsTransport` stub) and `DeadLetterQueue` (the `S3DeadLetterQueue` stub).

**Non-goals:** changing the shipped event lane's slim models; implementing the Step-Functions recompute fan-out; the production `KeyResolver` catalog/BOM expansion (separate #4 work).

## 3. Package & dependency layout

New peer service `services/event-publisher/`, package `trax-io-event-publisher` (python `trax_io_event_publisher`), mirroring forecasting/feature-store conventions (hatchling, uv, pytest `pythonpath=["src"]`, ruff line-length 100, py3.14).

```
services/event-publisher/
  pyproject.toml          # deps: pydantic>=2.6; optional: [dev]=pytest,ruff
                          #       [http]=fastapi,httpx  [schemathesis]=schemathesis
  src/trax_io_event_publisher/
    __init__.py           # public exports
    schemas.py            # EventEnvelope, EventKind, Producer, 7 payload models, semver + UUIDv7 validation, UNTRUSTED_FIELDS
    ids.py                # uuid7 helpers: new_event_id(), is_uuid7(s)  (stdlib uuid.uuid7)
    samples.py            # make_event(kind, **overrides) -> EventEnvelope  (valid-by-construction oracle)
    transport.py          # Transport Protocol, TransportResponse, FakeTransport, AsgiTransport([http]), HttpsMtlsTransport (deferred stub)
    dlq.py                # DeadLetterQueue Protocol, InMemoryDeadLetterQueue, S3DeadLetterQueue (deferred stub)
    publisher.py          # EventPublisher: retry/backoff/DLQ; PublishResult, PublishStatus
    endpoint.py           # fake_event_endpoint FastAPI app ([http]); response-code table + event_id dedup + /replay
    cli.py                # `trax-io-publisher emit ...` (optional, [http] for --to fake)
  tests/...
```

**Dependency direction (acyclic):** `trax-io-event-publisher` depends on nothing in-repo. `trax-io-agent-spine` gains a non-editable path dependency on `trax-io-event-publisher` (for the canonical schema) and houses the consumer adapter — so the dependency points one way (spine → event-publisher), keeping the canonical schema the single source of truth. After editing event-publisher, `uv sync --reinstall-package trax-io-event-publisher` in agent-spine.

## 4. Canonical schema (the wire contract as code)

All models `ConfigDict(frozen=True, extra="forbid")`. `EventKind` is a `StrEnum` of the 7 snake_case kinds. Envelope mirrors the contract exactly:

`EventEnvelope`: `event_id: str` (validated UUIDv7 via `is_uuid7`), `tenant_id: str` (kebab-case validated), `kind: EventKind`, `occurred_at: datetime` (tz-aware UTC), `produced_at: datetime` (tz-aware UTC), `schema_version: str` (semver `^\d+\.\d+\.\d+$`, default `"1.0.0"`), `producer: Producer`, `payload: <discriminated union by kind>`, `correlation_id: str | None = None`, `causation_id: str | None = None`. `Producer`: `{system: str, version: str, instance: str}`.

The 7 payloads carry full contract fields:
- **flight_completed**: tail, ac_type, destination, origin, flight_hours, cycles, flight_date
- **stock_moved**: pn, sn, from_location, to_location, from_condition, to_condition, qty, transaction_type, transaction_no, wo, moved_by
- **wo_scheduled**: wo, tail, ac_type, location, wo_type, scheduled_start, scheduled_end, estimated_duration_days, primary_eo
- **vendor_price_changed**: pn, vendor, condition, old_price, new_price, currency, old_lead_days, new_lead_days, preferred, effective_date
- **plan_published**: plan_id, plan_type, fleet, horizon_days, effective_from, revision
- **removal_recorded**: pn, sn, tail, ac_type, location, wo, task_card, **removal_reason** (untrusted), schedule_category, reason_category, removed_at
- **eo_published**: eo_number, ata_chapter, ata_subchapter, affected_fleet, affected_pn_pattern, criticality `Literal["AD","SB","FLEET_CAMPAIGN","OTHER"]`, compliance_due, compliance_threshold_hours, compliance_threshold_cycles, issued_by, **title** (untrusted), issued_at

Optional-with-defaults vs required follows the contract's source-system nullability; nullable source fields become `X | None = None`. The discriminated union validates that `payload`'s type matches `kind` (a `flight_completed` kind with a `stock_moved` payload is rejected). Round-trip is JSON via `model_validate_json` / `model_dump_json`.

**Security (SOC 2 / prompt-injection surface):** `UNTRUSTED_FIELDS = {"removal_recorded.removal_reason", "eo_published.title"}` is exported so consumers/observability scrub these free-text fields before any LLM or index use; the audit copy retains the original (contract requirement). Slice A exports the set and a `scrub(text)` helper + a test asserting the markers; enforcement at the LLM boundary is #4's concern.

**Versioning:** `schema_version_compatible(consumer_major: int, event_version: str) -> bool` returns True iff same major series — the consumer-side semver gate from the contract.

## 5. Transport seam

```python
class TransportResponse(BaseModel):   # frozen
    status_code: int
    retry_after_s: float | None = None
    body: dict | None = None

class Transport(Protocol):
    def send(self, *, tenant_id: str, body: bytes) -> TransportResponse: ...
```

- **FakeTransport** — scriptable: constructed with a queue of `TransportResponse`s (or a callable), records every `send`. Drives the producer's retry/terminal/duplicate paths deterministically with no I/O. Default response 202.
- **AsgiTransport** (`[http]`) — wraps `httpx.Client(transport=httpx.ASGITransport(app=fake_event_endpoint))`; real in-process HTTP round-trip to the FastAPI reference, exercising its validation + response codes without sockets or mTLS.
- **HttpsMtlsTransport** (deferred) — stub raising `NotImplementedError("Phase 2: real mTLS + AWS transport")`, documenting the seam.

## 6. Producer: retry / backoff / dead-letter

```python
class EventPublisher:
    def __init__(self, transport, *, dlq=None, max_attempts=7,
                 backoff_s=(1,2,4,8,16,32,60), sleep=time.sleep): ...
    def publish(self, event: EventEnvelope) -> PublishResult: ...
```

Per the contract's response-code table:
- **202, 409** → delivered (409 = duplicate `event_id`, idempotent success). Status `EMITTED`.
- **400, 401, 403** → terminal, **no retry** → DLQ, status `REJECTED`.
- **429** → respect `Retry-After` (sleep that long, else a default), retry; bounded by `max_attempts`.
- **5xx / transport error** → exponential backoff via `backoff_s`, retry up to `max_attempts`; on exhaustion → DLQ, status `DEAD_LETTERED`.

`sleep` is injected so tests pass a recording no-op and assert the exact backoff schedule without wall-clock delay. `PublishResult`: `{status: PublishStatus, attempts: int, last_status_code: int | None, dead_lettered: bool}`. `DeadLetterQueue` Protocol `put(event, reason)`; `InMemoryDeadLetterQueue.entries` for assertions; `S3DeadLetterQueue` deferred stub.

## 7. Fake event endpoint (`[http]`)

FastAPI app, `POST /v1/tenants/{tenant_id}/events`:
- Parse + validate the envelope against the canonical schema → **400** on schema error.
- **403** if path `tenant_id` ≠ envelope `tenant_id` (the contract's tenant-isolation check; cert-CN match is deferred).
- **409** if `event_id` already accepted for the tenant (idempotency store), marked delivered.
- Optional injectable rate-limiter hook → **429** with `Retry-After`.
- Else store + **202 Accepted**.
- `POST /v1/tenants/{tenant_id}/events/replay` re-emits stored events.
- In-memory `accepted: dict[(tenant_id, event_id)] -> EventEnvelope` for test assertions; tenant-scoped, no cross-tenant read.

Tested via Starlette `TestClient` / `AsgiTransport`. (FastAPI + httpx are already proven on this py3.14 env by the reco `--extra api` and agent-spine writeback code.)

## 8. Consumer adapter (in agent-spine event_lane)

New `services/agent-spine/src/trax_io_spine/event_lane/canonical_adapter.py`:

```python
def to_domain_event(canonical: EventEnvelope) -> DomainEvent: ...
```

A mechanical per-kind down-projection: copies envelope `tenant_id`, `event_id`, `occurred_at`, `schema_version`, maps `kind`, and selects the slim payload's fields from the rich payload (the slim fields are a strict subset of the canonical fields for every kind). This is the bridge that lets the shipped, unchanged event lane ingest real contract events. agent-spine adds the `trax-io-event-publisher` path dependency to import the canonical schema. A round-trip test proves: `make_event(kind) → to_domain_event → EventLaneHandler.handle → OrchestrationResult` for the kinds the `DirectKeyResolver` resolves (stock_moved, removal_recorded) and that fan-out kinds adapt without error (resolve to no-ops, as today).

## 9. Testing strategy

- **Schema tests** — each of 7 kinds round-trips JSON; `extra="forbid"` rejects unknown fields; kind/payload mismatch rejected; UUIDv7 + semver + kebab-case validators accept/reject; `UNTRUSTED_FIELDS`/`scrub` asserted; `schema_version_compatible` table.
- **Producer tests** (FakeTransport) — 202 success; 409 idempotent success; 400/401/403 terminal → DLQ/REJECTED no retry; 5xx exhausts `max_attempts` with the exact backoff schedule (recorded `sleep`) → DEAD_LETTERED; 429 honors Retry-After then succeeds.
- **Endpoint tests** (`[http]`, TestClient) — 202 happy path; 400 bad schema; 403 tenant mismatch; 409 duplicate event_id; 429 from the rate-limit hook; `/replay`.
- **Round-trip integration** (`[http]`) — producer → AsgiTransport → endpoint (202 then 409 on re-send).
- **Consumer-adapter integration** (in agent-spine) — canonical → slim → handler → result.
- **Schemathesis** (`[schemathesis]`, best-effort) — property-based fuzzing of the FastAPI OpenAPI schema. Gated behind its own extra; **Task 0 grounding verifies it installs on py3.14** — if it does not, it drops to a documented ROADMAP follow-up and the hand-written contract tests (which do not depend on it) remain the gate.

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Schemathesis may not install on py3.14 | Gated behind `[schemathesis]`; core contract tests are plain pytest; Task 0 probes installability, else defer. |
| Canonical/slim drift over time | Single source of truth = canonical package; adapter is the only bridge; adapter test fails loudly if a slim field loses its canonical source. |
| Over-building producer mechanics never used by v1 | Retry/backoff/DLQ are the contract's normative producer behavior and the eMRO team's executable reference — in scope by the approved decision. mTLS/Kinesis/S3 stay deferred stubs. |
| circular dep spine ↔ event-publisher | One-way: event-publisher depends on nothing in-repo; the adapter lives in spine. |

## 11. Deliverables

- `services/event-publisher/` package (schema, transport, producer, dlq, endpoint, samples, cli) with full test suite, ruff-clean.
- agent-spine consumer adapter + round-trip test + the new path dependency.
- ADR recording the canonical-schema-source-of-truth + harness-slice decision.
- CLAUDE.md run/test row; ROADMAP #3 slice-A done + Java/AWS deferrals; TASKS.md entry.
