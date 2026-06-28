# ADR-0008: Event-driven recompute is a consumer-side `EventIngestor` in agent-spine

**Date:** 2026-06-27
**Status:** Accepted
**Context project:** #4 Agent Spine / #3 consumer integration (event ingestion slice)

## Context

After #3 slice A, every piece of the real-time recompute loop existed but nothing wired them together: the canonical `EventEnvelope` + producer oracle (`trax_io_event_publisher`), the `canonical_adapter.to_domain_event` down-projection, and `EventLaneHandler.handle(DomainEvent) → OrchestrationResult`. The design calls for an **event-triggered recompute** path (Events → recompute of `(ROP, EOQ, SS, Max)`) alongside the nightly extract, and the contract specifies **at-least-once delivery with idempotency via `event_id`**.

The natural question was *where* the ingestion connector lives. The event-publisher already ships a `fake_event_endpoint` — but having it drive the spine handler would **invert the one-way dependency** (event-publisher depends on nothing in-repo; agent-spine depends on it) and create an import cycle.

## Decision

Build event-driven recompute as a **consumer-side `EventIngestor` in agent-spine** (`event_lane/ingestor.py`), plus a `trax-io-spine ingest` CLI that replays a canonical-event JSONL end to end. The ingestor:

- **decodes** canonical `EventEnvelope`s (importing the schema from `trax_io_event_publisher` — the allowed direction);
- **dedups by `event_id`** (the contract's idempotency guarantee, realized on the consumer side) — a repeat `event_id` is a `DUPLICATE`, no second recompute;
- **adapts** via `to_domain_event` and **handles** via the unchanged `EventLaneHandler`;
- **classifies** each event: `PROCESSED` (recompute produced ≥1 recommendation), `NO_OP` (no resolvable keys/bundles — e.g. fan-out kinds or cross-tenant), `DUPLICATE`, or `INVALID` (failed canonical validation → dead-lettered);
- **never raises** — a malformed event is dead-lettered via a `DeadLetterSink` and returned as an `INVALID` outcome, mirroring the producer/endpoint contract that ingestion failures are recorded, not propagated;
- rolls outcomes up into an `IngestReport` (counts + summed recompute totals).

**Dependency direction stays one-way:** ingestion lives entirely in agent-spine; it imports the canonical schema, never the reverse. The `EventLaneHandler`, the slim `DomainEvent` models, the canonical schema, and `canonical_adapter` are all unchanged.

This mirrors the project's deterministic-default / seam-first pattern: the in-process `EventIngestor` + `InMemoryDeadLetterSink` are the local impls; the HTTP ingestion service and S3 dead-letter slot in behind the same seams.

## Consequences

**Positive**
- The real-time recompute loop is now demonstrable end to end and fully local: `make_event` (the #3 producer oracle) → JSONL → `trax-io-spine ingest` → recommendations, with idempotent dedup and dead-lettering — no AWS. (62 agent-spine tests; live CLI verified.)
- Consumer-side idempotency means the contract's at-least-once delivery is safe: re-delivered events are dedup'd, not re-recomputed.
- The HTTP ingestion service and AWS transport are pure additions behind the `EventIngestor` / `DeadLetterSink` seams.

**Negative / deferred**
- The dedup `seen` set is in-memory and unbounded — fine for batch replay; a bounded/persistent dedup store is a deferred follow-up.
- `NO_OP` covers fan-out kinds (`flight_completed`, `eo_published`, …) until the production `KeyResolver` catalog/BOM expansion lands; those events validate and adapt but resolve to no keys today.
- No HTTP ingestion endpoint, Step Functions, EventBridge, or per-tenant Kinesis in this slice (all AWS, deferred).

## Alternatives considered

1. **Drive the spine from the event-publisher's `fake_event_endpoint`.** Rejected: inverts the one-way dependency into a cycle. Consumer-side ingestion in agent-spine keeps the graph acyclic.
2. **Ship the HTTP ingestion service now.** Deferred: adds a FastAPI server (and fastapi/httpx) to agent-spine for no local-verifiability gain over the CLI; it slots cleanly behind the `EventIngestor` seam later.
3. **Skip dedup (rely on downstream `idempotency_key`).** Rejected: the contract guarantees at-least-once, so consumer-side `event_id` dedup is the faithful place to prevent duplicate recomputes.
