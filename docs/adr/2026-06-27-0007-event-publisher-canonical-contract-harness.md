# ADR-0007: #3 event publisher ships as a canonical wire-contract + fake_emro harness (slice A)

**Date:** 2026-06-27
**Status:** Accepted
**Context project:** #3 eMRO Outbound Event Publisher (slice A)

## Context

The 2026-04-14 #3 sub-plan frames the event publisher as a **Java add-on inside eMRO** (Oracle triggers / CDC → a Spring Boot drainer with mTLS) plus **AWS transport** (mTLS endpoint → EventBridge → per-tenant Kinesis → Iceberg CDC + Step Functions recompute). None of that is buildable or locally verifiable in this Python monorepo, and the legacy Java reference (`~/ptcwebservice/`) is batch file-export with no outbound eventing — i.e. greenfield.

Two facts shaped the slice:

1. **The consumer already exists.** The agent-spine `event_lane/` (built earlier) consumes a **slim** `DomainEvent` (e.g. `StockMovedPayload{pn, from_location, to_location, qty}`). The Apr-14 contract is **richer** — `stock_moved` carries 11 fields, and the envelope adds `event_id` (UUIDv7), `produced_at`, `producer`, `correlation_id`/`causation_id`. Two sources of truth existed.
2. **The contract, the plan, and ADR-0003 all name a Trax-side contract-first deliverable** — a canonical schema + `fake_event_publisher` + `fake_event_endpoint` + shared contract tests — the same slicing used for #1 (extract) and #6 (writeback `fake_emro`).

## Decision

Build #3 slice A as `services/event-publisher/` (`trax_io_event_publisher`): the **canonical wire contract expressed as executable code** plus a `fake_emro` producer/endpoint **test harness**, with the Java/AWS body deferred behind seams.

- **Canonical schema = single source of truth.** Full-fidelity frozen pydantic v2 (rich envelope + all 7 rich payloads, UUIDv7/semver/kebab-case validators, smart-union with a kind↔payload after-validator, `UNTRUSTED_FIELDS` markers on `removal_reason`/`title`). A `test_contract_examples.py` parses all 7 contract JSON examples verbatim as a permanent fidelity guard.
- **Consumer reconciliation is one-way via an adapter, not a rewrite.** The shipped slim `DomainEvent` models are never edited; a `to_domain_event` adapter in the event lane down-projects a canonical event to the slim one (the slim fields are a strict subset for every kind). **Dependency points one way:** event-publisher depends on nothing in-repo; agent-spine depends on it.
- **Producer behind a `Transport` seam.** `EventPublisher` implements the contract's retry / exponential-backoff / dead-letter and response-code table (202/409→emitted, 400/401/403→terminal, 429→Retry-After, 5xx/transport-error→bounded backoff→dead-letter), never raising to the caller. `FakeTransport` (scriptable, deterministic via injected `sleep`) + `AsgiTransport` (in-process httpx ASGI round-trip) are the local impls; `HttpsMtlsTransport`/`S3DeadLetterQueue` are deferred `NotImplementedError` stubs.
- **`fake_event_endpoint`** (FastAPI) enforces 202/400/403/409 + idempotency + `/replay`.

**Grounded dependency decisions:**
- `event_id` uses **stdlib `uuid.uuid7()`** (native on Python 3.14) — no fragile UUIDv7 dependency.
- **Schemathesis is deferred.** It pins an older fastapi that uninstalls the `http` extra on Python 3.14 (the plan's Task-0 risk materialized). The slice ships hand-written conformance tests (`test_conformance.py`) as the gate; property-based testing is a ROADMAP follow-up.
- **Wire-fidelity catch:** `transaction_no` is a JSON **integer** in the contract (`88412`); it was initially typed `str` and failed to parse the contract's own example — exactly the drift this harness exists to prevent. Now `int`, with a regression test.

This mirrors the project's repeated pattern (ADR-0002 feature store, ADR-0005 agent spine, ADR-0006 forecasting): Protocol-first, deterministic-default, heavier/cloud impl injected behind the same seam.

## Consequences

**Positive**
- The wire contract is now executable and machine-checkable; the future eMRO Java producer and the Trax consumer validate against the same canonical schema, and `test_contract_examples.py` fails loudly on any drift.
- The shipped event lane ingests real contract events with **zero change to its tested models** (adapter-only reconciliation).
- Fully locally verifiable: 64 event-publisher tests + agent-spine 48 (incl. the canonical→adapter→handler round-trip), no AWS.
- Later work (Java triggers/CDC, real mTLS, EventBridge/Kinesis/S3 audit, operator UI) is pure addition behind `Transport`/`DeadLetterQueue`.

**Negative / deferred**
- The producer's delivery mechanics are an executable **reference** for the eMRO team, not the production producer (which is Java).
- `_check_utc` accepts any tz-aware datetime, not strictly UTC (matches the contract's leniency; can tighten later).
- Schemathesis property testing deferred pending a py3.14-compatible pin.

## Alternatives considered

1. **Enrich the slim event_lane models to full fidelity (event_lane as source of truth).** Rejected: more churn on shipped/tested code and couples the external wire contract to the consumer's internal model. The adapter keeps the boundary clean.
2. **A separate SageMaker/AWS producer now (faithful to the 2014-plan framing).** Rejected for v1: AWS-coupled, not locally verifiable; the classical contract + harness runs in-process.
3. **Skip the canonical package; just add a producer Protocol to the event lane.** Rejected: leaves the rich wire contract un-executable and doesn't de-risk the Java build.
