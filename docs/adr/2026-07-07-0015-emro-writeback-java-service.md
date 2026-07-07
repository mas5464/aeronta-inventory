# ADR-0015: One domain core, two facades; Java conforms to the Python writeback contract

**Date:** 2026-07-07
**Status:** Accepted
**Context project:** #6 eMRO Writeback REST API (real-eMRO Java track, slice 1)

## Context

Sub-project #6's local hardening slice ([ADR-0010](2026-06-28-0010-audited-writeback-seam.md)) proved the writeback seam's shape — provenance, rollback, shadow-mode — entirely against `fake_emro`. It deliberately deferred the real half: an actual service that authenticates, validates, and writes `PN_INVENTORY_LEVEL` in the customer's Oracle eMRO instance. That real half is a Java service, because eMRO itself is Java/Oracle and the release train (per the legacy `~/ptcwebservice/PTCWebService/` reference and the ARMAC `ROLEOQUpdateService`) only ships Java add-ons.

Two upstream shapes had to be satisfied simultaneously and they don't naturally agree:
- The **PRD's** own batch write-back surface (`POST /api/v1/stock-levels`, camelCase, `{runId, transactionId, items}`), inherited from the legacy PTC integration pattern.
- The **Trax IO #6 seam** already built and tested in Python (`services/agent-spine`'s `RestWritebackClient` + `fake_emro`), which the Supervisor's writeback specialist calls today and which is snake_case, per-key, and provenance-rich.

Both facades must apply the same guardrails (min≤max, principal restrictions, shelf-life/hazmat clamps) and the same idempotency/audit/ledger machinery — duplicating that logic per facade was the primary risk to design against.

## Decision

Build one framework-free domain core, `StockLevelWriter`, fed by three thin entry points (Facade 1, Facade 2, Kafka), all normalizing to a canonical `WritebackCommand` before the core ever sees them.

- **Clean-room Quarkus 3 / Java 21 module** at `services/emro-writeback-java/`, not a fork of the legacy `PTCWebService` or ARMAC codebases. JPA entities were **lifted** field-for-field from the ARMAC reference (byte-for-byte fidelity verified) since the eMRO schema itself doesn't change; business rules were **ported test-first** from the spec's rule table, not copied from legacy Java.
- **`StockLevelWriter`** is the single place validation, upsert, audit-row insert, and ledger-row insert happen, each item in its own `@Transactional(REQUIRES_NEW)` transaction. A service-owned `WRITEBACK_LEDGER` table (new Flyway-managed table — eMRO's own tables are never DDL'd) gives at-least-once delivery an **effectively-once** outcome via a unique `idempotency_key`, plus a unique `(tenant, pn, location, version)` chain for optimistic versioning with a bounded (3-attempt) retry on conflict.
- **Canonical `WritebackCommand`** is what both REST facades and the Kafka consumer produce; `StockLevelWriter.writeItemDedup` is the one method any of them calls.
- **JWT on every endpoint** except `/q/health*` (`writeback:write` for apply/batch/ingest, `writeback:read` for history), consistent with the design's auth posture even though the real IdP integration is out of scope for slice 1.
- **Trust-caller-record-provenance**: the service records the tenant/principal/tier/provenance-id it is given by the caller (Trax IO Supervisor or PRD batch caller) rather than re-deriving or re-validating identity claims beyond the JWT itself — eMRO-side business-rule re-validation is explicitly deferred (see [ADR-0010](2026-06-28-0010-audited-writeback-seam.md)'s consequences, unchanged here).
- **Facade 1 (`api/traxio/`)** is contract-extracted from — and conforms to — the already-shipped Python `RestWritebackClient`/`fake_emro`, never the reverse: exact paths, snake_case field names, and status strings were pulled from the Python source during planning, and any true conflict is resolved by an explicit documented deviation (below), not a silent behavior change.
- **Facade 2 (`api/batch/`)** implements the PRD's camelCase batch surface directly (`items: [...]`, explicit fields — the legacy ARMAC string-replace hack is dropped).

## Deviations & discoveries

Carried from the plan header's three documented deviations, plus what implementation surfaced:

1. **No `409 deferred_open_order` in slice 1.** Facade 1 never emits it — open-order deferral is enforced upstream by the Supervisor before a write request ever reaches this service. `RestWritebackClient` handles the status if seen but doesn't require it, so this is a safe non-emission, not a contract break.
2. **`agent_version` self-identifies as `"emro-writeback-java/1.0"`.** `fake_emro` hardcodes `"agent-spine-v1"` for its own in-memory target; the field identifies *the writer*, and each writer reporting its own identity is more correct than the Java service borrowing the Python fake's string.
3. **History is served ledger-only in slice 1.** The spec originally called for `PN_INVENTORY_LEVEL_AUDIT` to supplement `WRITEBACK_LEDGER` for out-of-band writers (§4.1). Fabricating a `version` int for an audit-only row that never went through the ledger needs the rollback design to land first — deferred to slice 2 alongside rollback itself (spec §4.1 amended, see the companion change in this commit).
4. **Tier wire format is the `IntEnum` int, discovered mid-implementation.** The plan assumed tier was string-only on the wire; `services/agent-spine`'s `AutonomyTier` is actually a Python `IntEnum`, so the real wire value is an int. `TierMapper` accepts both the int and the tier name string (defensive, since a future Python-side change could re-introduce the string form) and `toWire` always emits the int, matching current Python behavior exactly.
5. **Rejected rows are not ledgered — by design.** A row that fails validation never consumes an idempotency key, so a corrected retry of the same `runId:rowId` can succeed. Only applied and shadowed writes are ledgered.
6. **`WRITEBACK_LEDGER`'s `V1` migration was amended in place twice, pre-deployment.** First to name the two unique constraints explicitly (so duplicate-key vs. version-conflict failures could be told apart by constraint name rather than a generic SQL exception), then to add the `PROVENANCE_ID` column the schema had been missing since Task 5. Both amendments landed before any real deployment, so amending `V1` in place — rather than adding `V2`/`V3` — was judged safe and simpler.

## Consequences

**Positive**
- One validation/idempotency/audit/ledger implementation serves both facades and Kafka — no drift risk between the PRD batch path and the Trax IO seam path.
- Facade 1 is provably wire-compatible with the already-shipped `RestWritebackClient`/`fake_emro`, so the Python Supervisor's writeback specialist can point at this service with no client-side changes.
- 65 tests green (subagent-driven TDD + adversarial review per task), including an env-gated `oracle19c` connect-only smoke test that caught a real bug pre-live (a `JUnit AssertionError` bypassing the smoke test's restore-on-failure path — fixed by widening the catch to `Throwable`).

**Negative / carried forward (tracked in ROADMAP #6)**
- **Kafka infra-retry is effectively unreachable.** `StockLevelWriter`'s broad exception catch folds a down database into per-row `ERROR` results (published to the results topic, fail-safe, no silent drop) instead of throwing, so retry/DLQ semantics only fire for `BatchProcessor`-level failures, not per-item DB connectivity loss. Fixing this needs an exception taxonomy (let connectivity-class exceptions propagate) — judged too risky to rework inside the already-reviewed core during slice 1; belongs with a hardening slice.
- **Audit-row `DATE` precision on real Oracle.** The audit primary key includes a `CREATED_DATE` component with only second-level precision; two same-second writes by the same principal to the same key collide (fails safe as a `500 ERROR`, never silently overwrites). Needs sub-second precision or a narrower audit PK before high-frequency real-eMRO writes are safe.
- **Results-emitter redelivery loop has no backoff.** If publishing a result to the results topic itself fails, the current retry is a tight loop; idempotency keeps it DB-safe, but it should gain backoff and is only documented, not fixed, in slice 1.
- Rollback, requisitions, transfers, replay tooling, and the production IdP/broker/deploy path are all deferred to slice 2 (see ROADMAP #6 and the plan's own "Slice 2" note).

## Alternatives considered

1. **Two independent services (one per facade).** Rejected: guarantees drift between the PRD batch rules and the Trax IO seam rules, and doubles the idempotency/audit/ledger surface that must stay correct under concurrent writes.
2. **Fork the legacy `PTCWebService`/ARMAC Java codebase directly.** Rejected per the global constraint (`CLAUDE.md`): those are read-only reference implementations. Entities were lifted (the schema is unchanged, so re-deriving them from scratch would be pure risk with no benefit); business logic was ported test-first from the spec, not copied.
3. **Make Facade 2 the canonical shape and adapt Facade 1 to it.** Rejected: Facade 1 has a shipped, tested Python client on the other end (`RestWritebackClient`/`fake_emro`); changing it would ripple into `services/agent-spine` for no functional gain. The Java side conforms to the existing contract, never the reverse.
