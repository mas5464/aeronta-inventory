# Sub-plan #3 — eMRO Outbound Event Publisher Implementation Plan

**Goal:** Ship the Trax-authored eMRO add-on module that emits the seven domain events (`flight_completed`, `stock_moved`, `wo_scheduled`, `vendor_price_changed`, `plan_published`, `removal_recorded`, `eo_published`) from inside a customer's eMRO instance to the Trax IO event endpoint, conforming to the contract in `docs/contracts/2026-04-14-emro-event-publisher-contract.md`.

**Owner:** eMRO product team (primary) + Trax IO platform (secondary).

**Format:** Unlike sub-plans #1, #2, #4, #5 which live in Trax-owned repos with TDD task lists, this plan is an **integration-contract-driven work plan** for the eMRO team. eMRO has its own Java test conventions and its own release train; we hand them acceptance tests, not Python TDD steps.

**Tech Stack:** Java 17, Spring Boot (aligned with eMRO's existing stack), Oracle Database triggers or Oracle Advanced Queueing (AQ) for change capture, mTLS-HTTPS for outbound, JSON schema validation via `networknt/json-schema-validator`.

---

## Acceptance criteria (the eMRO team implements against these)

1. The add-on installs as a single deployable unit into the customer's eMRO instance (Spring Boot fat-JAR + one DB migration).
2. It captures the seven domain events from eMRO's operational tables within 60 seconds of the underlying business event (5 minutes for `wo_scheduled`, `plan_published`, `eo_published`).
3. Each emitted event conforms to the JSON Schema for its kind (published at `docs/contracts/schemas/`) and validates against the AsyncAPI spec.
4. Delivery is at-least-once with `event_id` deduplication on the Trax IO consumer side.
5. Events are emitted in `produced_at` order per `(tenant_id, kind)`.
6. mTLS outbound using a per-tenant client certificate issued by Trax.
7. Producer retry per the contract: exponential backoff, 7 attempts, then dead-letter to a local persistent queue. DLQ has a UI for the tenant operator.
8. Every emitted event is logged to an append-only audit table in the customer's eMRO DB with a 7-year retention policy (enforced by a scheduled purge job that *moves* old rows to S3 archive rather than deleting).
9. The add-on surfaces one operator-facing dashboard: per-event-kind emission count (1h, 24h, 7d), DLQ depth, last-successful-send timestamp, current schema version per kind.
10. Feature flag per event kind: the tenant operator can disable any kind independently (used during incident response and for staged rollout of new kinds).

## Implementation phases

### Phase 0: Design review + contract sign-off (2 weeks)

- Read sub-plan #4 Agent Spine's consumer expectations.
- Read `docs/contracts/2026-04-14-emro-event-publisher-contract.md` front to back.
- Joint design session: eMRO + Trax IO + SecOps + lighthouse customer CIO.
- Sign-off on: transport (HTTPS + mTLS), event ordering, DLQ behavior, feature-flag model, schema versioning policy.
- **Exit:** Contract document signed by all four parties.

### Phase 1: Change-capture source selection (2 weeks)

eMRO emits change events from one of three possible sources. Decision pending performance tests:

- **Option A — Oracle database triggers.** One trigger per source table (`AC_ACTUAL_FLIGHTS`, `PN_INVENTORY_HISTORY`, `WO`, `WO_ENGINEERING_ORDER`, `PN_VENDOR_PRICE`, `AC_PN_TRANSACTION_HISTORY`). Each trigger inserts a row into a `TRAX_EVENT_OUTBOX` queue table; a Spring Boot `@Scheduled` component drains the queue and POSTs to Trax IO.
- **Option B — Oracle Advanced Queueing (AQ).** Same idea with Oracle's native message queue. Better delivery guarantees, more operational complexity.
- **Option C — eMRO application-layer events.** Emit from the Java service layer that writes the table. Decoupled from DB triggers but requires changes to every eMRO write path — largest code footprint.

**Recommendation:** A (triggers + outbox table) for speed to market. Migrate to C in v1.1 if DB-trigger load becomes an issue at scale.

**Exit:** Benchmark against representative customer data volume shows <5% DB load increase; decision locked.

### Phase 2: Trigger + outbox implementation (4 weeks)

- DB migrations create `TRAX_EVENT_OUTBOX` (partitioned by date for purge), `TRAX_EVENT_AUDIT` (append-only, 7yr retention), `TRAX_EVENT_FEATURE_FLAGS`.
- One trigger per source table; trigger logic:
 - `AFTER INSERT/UPDATE` on the source row.
 - Construct event payload from `NEW` row values.
 - Insert into `TRAX_EVENT_OUTBOX` with `status = 'PENDING'`.
- Triggers are feature-flagged: `TRAX_EVENT_FEATURE_FLAGS` rows can disable any kind independently without dropping the trigger.
- Unit test each trigger against a schema-matching test fixture.
- Integration test: a full eMRO write (new flight record, new removal, etc.) produces the expected outbox row.

### Phase 3: Outbox drainer + HTTP client (4 weeks)

- Spring Boot `@Scheduled` component polls `TRAX_EVENT_OUTBOX WHERE status = 'PENDING' ORDER BY produced_at` in batches of 100.
- HTTP client: `java.net.http.HttpClient` with:
 - mTLS configured via customer-specific keystore (location from application properties).
 - Per-request `Idempotency-Key` derived from `event_id`.
 - Retry with exponential backoff per contract.
 - Circuit breaker (Resilience4j) to prevent cascading failures into the DB.
- On 202 response: `UPDATE TRAX_EVENT_OUTBOX SET status = 'SENT', sent_at = SYSDATE WHERE event_id = ?`.
- On 4xx response: `UPDATE ... status = 'FAILED_NON_RETRYABLE'`; alert the operator.
- On 5xx/network error after retry exhaustion: `UPDATE ... status = 'DLQ'`; alert.
- Every status transition mirrored to `TRAX_EVENT_AUDIT`.

### Phase 4: Operator UI (2 weeks)

- Embedded in eMRO admin UI.
- Shows per-kind counts (1h/24h/7d), DLQ depth, feature-flag status, last-successful-send.
- "Replay DLQ" button — calls Trax IO's `POST /v1/tenants/{tenant_id}/events/replay` endpoint which temporarily accepts non-strict ordering.
- "Disable kind" toggle.

### Phase 5: Contract testing (3 weeks)

- Pull the shared contract test package from sub-plan #4 (`trax-io-event-contract-tests`). Run against the eMRO add-on in a staging environment.
- Schemathesis property-based tests run against the eMRO staging endpoint nightly; failures page the eMRO on-call.
- Load test: 1000 events/sec sustained for 30 minutes without DB contention spikes.

### Phase 6: Customer rollout (4 weeks)

Staged rollout with the lighthouse customer:
- Week 1: deploy with every kind feature-flagged OFF. Verify admin UI, outbox mechanics, DLQ behavior.
- Week 2: enable `flight_completed` only. Verify volumes match expectations; Trax IO consumer observability shows expected traffic.
- Week 3: enable `stock_moved`, `removal_recorded`, `wo_scheduled`. Highest-volume kinds.
- Week 4: enable `vendor_price_changed`, `plan_published`, `eo_published`.
- Exit criteria: 14 days continuous operation with zero DLQ entries from contract or schema issues.

### Phase 7: Ship in eMRO release train (aligned with release calendar)

Module is cut into an eMRO release candidate, regression-tested against eMRO's standard test matrix, documented in eMRO release notes, and made available for customer upgrades.

---

## Deliverables from the eMRO team

1. `trax-event-publisher-X.Y.Z.jar` — the Spring Boot add-on fat-JAR.
2. DB migration scripts (Liquibase or Flyway, matching eMRO's convention).
3. Installation guide for customer DBAs.
4. Admin UI screenshots for customer operator onboarding.
5. Runbook for DLQ recovery.
6. Schemathesis test report showing 0 contract violations on latest build.
7. Load-test report.
8. Release notes entry for the eMRO version carrying this module.

## Deliverables from the Trax IO platform team (supporting)

1. JSON Schemas for all seven event kinds (published at contract path).
2. AsyncAPI 2.6 spec document.
3. Per-tenant mTLS certificate issuance runbook (joint SecOps + Trax platform).
4. `trax-io-event-contract-tests` shared Java+Python test suite.
5. Trax IO consumer endpoint documentation + SLO commitments.
6. Test event generator CLI (`fake-emro-event-publisher`) that eMRO developers use for local integration work before their production wiring is ready.

---

## Risks + mitigations

- **DB-trigger performance impact** — benchmark in Phase 1; fallback to Option C (app-layer) if triggers cost > 5% on write-heavy workloads.
- **Customer DBA skepticism of triggers on production tables** — mitigated by shipping the security review package, clear load benchmarks, and an opt-out to Option C for unusually conservative customers.
- **eMRO release train slip** — feature-flag-off default means the module can be in the release long before it is activated at any customer site; reduces release coupling.
- **Schema drift** — contract tests run on every PR in the eMRO repo; Trax IO drift also breaks the same tests; drift is impossible to hide.

## Estimated timeline

- Phase 0–4: 12 weeks elapsed for eMRO team (2 engineers).
- Phase 5: 3 weeks parallelized with Phase 4 tail.
- Phase 6: 4 weeks elapsed.
- Phase 7: per eMRO release cadence (typically 8–12 weeks from feature complete to GA).

**Earliest GA in a customer's eMRO:** ~6 months from kickoff.

**Impact on sub-plan #4 schedule:** The Agent Spine's event lane is deferred until this module ships in the lighthouse customer's eMRO. Nightly-only operation is the fallback until then. This is explicitly acceptable per the design §4.1 (ingestion hybrid E).
