# eMRO Write-Back Service (Java) — Slice 1 Design

**Date:** 2026-07-06
**Status:** Approved (brainstorm 2026-07-06)
**Realizes:** Sub-project **#6 — eMRO Writeback REST API** (real-eMRO track) + PRD *Inventory Optimizer Write-Back Service* Phases 0–1
**Sources:**
- PRD: `~/trax-mgmt-armac_interfaces/PRD_Inventory_Optimizer_Writeback.md` (Draft v1.0, 2026-07-06)
- Reuse analysis: `~/trax-mgmt-armac_interfaces/INVENTORY_WRITEBACK_REUSE_ANALYSIS.md`
- Reference code: `~/trax-mgmt-armac_interfaces/ROLEOQUpdateService/` (ARMAC; Jakarta EE 11 / WildFly 39 / Java 17 — not a git repo)
- Existing seam: `services/agent-spine/src/trax_io_spine/writeback/` (`RestWritebackClient`, `fake_emro` contract, [ADR-0010](../../adr/2026-06-28-0010-audited-writeback-seam.md))

---

## 1. Summary

A single Quarkus 3 / Java 21 service, `services/emro-writeback-java/`, that applies Inventory Optimizer recommendations to the Trax eMRO Oracle database — slice 1 covers the **stock-level domain** (`PN_INVENTORY_LEVEL` + audit) end-to-end with validation, idempotency, full audit, enforced JWT auth, and per-row result reporting, ingesting over **both** synchronous REST and **Kafka**.

It is a clean-room implementation (approach A): the ARMAC JPA entities lift nearly verbatim; the proven `StockLevelData` business rules are **re-implemented as a framework-free domain core, with each rule pinned as a named test first**. The ARMAC `transactionId` JSON string-replace hack and its DTO shapes are not carried over.

This service is the **real-eMRO half of Trax IO sub-project #6**, which is currently stubbed by `fake_emro` on the Python side. The Python `RestWritebackClient` must plug into it unchanged.

## 2. Decisions (locked during brainstorm)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Code home | Trax IO monorepo, `services/emro-writeback-java/` | Ties the deliverable to sub-project #6; one repo carries spec→plan→code |
| D2 | API contract | **Both facades from day one** — Trax IO #6 seam + PRD batch surface — over one domain core | Agent-spine plugs in unchanged; ARMAC-style batch callers and full-fleet runs get the PRD surface |
| D3 | Kafka | **In slice 1**, tested via Quarkus Dev Services (Redpanda testcontainer) | Async-first per PRD; production broker availability (PRD open Q2) stays a deployment concern |
| D4 | Auth | Quarkus OIDC **bearer-JWT enforced on every endpoint** from day one; dev issuer in tests; production IdP = config. PTC LDAP/license checks out of scope | FR-9 without coupling to Oracle internals |
| D5 | Approval gate | **Trust the authenticated caller; require + record provenance** (runId, source, tier/approver where present). No re-adjudication | Approval is enforced upstream (Trax IO guardrail/tiers). PRD open Q3 resolved |
| D6 | #6 surface in slice 1 | **Apply-write + get-history**; rollback in slice 2; shadow mode stays Python-side | History is cheap off the audit table; rollback is cheap once history exists; shadowed writes never reach eMRO by design |
| D7 | Test DB | **Oracle testcontainer (gvenzl/oracle-free) for automated tests + opt-in env-gated smoke profile against local `oracle19c` eMRO schema** | Portable CI + real-schema validation before deploy. The `oracle19c` container is never managed by this project — connect only |
| D8 | Architecture | **Approach A: clean-room single Maven module**; entities lifted, logic ported test-first; no multi-module split | No EJB baggage; boundaries by package; ARMAC fidelity preserved as executable tests, not copy-paste |

## 3. Architecture

### 3.1 Module & packages

```
services/emro-writeback-java/          (Quarkus 3.x · Java 21 · single Maven module)
└─ src/main/java/trax/io/writeback/
   ├─ domain/        # Framework-free core: StockLevelWriter, ported ARMAC rules,
   │                 # WritebackCommand / ItemResult / ResultStatus, numeric policy
   ├─ persistence/   # JPA entities lifted from ARMAC: PnInventoryLevel(+PK),
   │                 # PnInventoryLevelAudit(+PK), PnMaster, LocationMaster,
   │                 # SystemTranCode(+PK), ProfileMaster
   │                 # + WritebackLedger (service-owned) + repositories
   ├─ api/traxio/    # Facade 1 — Trax IO #6 seam (single-key apply, GET history)
   ├─ api/batch/     # Facade 2 — PRD batch surface (runId/items[] → per-row results)
   ├─ ingest/        # Kafka consumer + results publisher + DLQ
   └─ security/      # JWT/OIDC config (mostly application.properties)
```

**One domain core, three entry points.** Both REST facades and the Kafka consumer normalize into the same canonical `WritebackCommand` (one item = one `{pn, location}` write + provenance) and call the same `StockLevelWriter`. Facades stay thin: serialization + HTTP semantics only.

### 3.2 Data flow (per item)

```
normalize → validate → idempotency check → [REQUIRES_NEW tx:
    upsert PN_INVENTORY_LEVEL
  + insert PN_INVENTORY_LEVEL_AUDIT
  + insert WRITEBACK_LEDGER row ]
→ per-row result (sync JSON reply | results topic)
```

### 3.3 Idempotency ledger

- Service-owned table **`WRITEBACK_LEDGER`**, unique key `(run_id, row_id)`, plus columns for `pn`, `location`, provenance (source, tier, approver, caller principal), outcome, old→new values (JSON), and timestamps.
- Managed by **Flyway in the service's own schema** — this service **never** issues DDL against eMRO tables.
- The ledger insert shares the item's transaction: at-least-once delivery + the unique constraint = effectively-once application. A replayed key trips the constraint → `SKIPPED_DUPLICATE` result, no double-apply (FR-4).
- Outcome-per-key in the ledger is what makes FR-10 replay implementable in the hardening slice.

### 3.4 Transactions

Per-item `@Transactional(REQUIRES_NEW)`. One bad row fails that row only, never the batch (FR-6). No batch-wide transaction exists. An unexpected exception rolls back that item's write **and** its ledger row, so a retry can legitimately re-attempt.

## 4. Contracts

### 4.1 Facade 1 — Trax IO #6 seam (`api/traxio/`)

Contract goal: `RestWritebackClient` (agent-spine) works against this service **unchanged**, and the behaviors pinned by the `fake_emro` contract tests hold.

- **Apply write** — single-key PUT. Body: proposed `(ROP, EOQ, SS, Max)` + full provenance (tenant, decision/run id, tier, agent version, forecast provenance id). Response: applied/rejected + reason + old→new values, in the shape `RestWritebackClient` expects.
- **Get history** — GET per `{pn, location}`: served from `PN_INVENTORY_LEVEL_AUDIT` (values, timestamps) joined with `WRITEBACK_LEDGER` (runId, tier, source, principal).
- **Not in slice 1:** rollback (slice 2). **Never here:** shadow mode (enforced Python-side; shadowed writes never reach this service).

> **Contract-extraction rule:** exact paths, field names, and status semantics are extracted from `services/agent-spine`'s `RestWritebackClient` + `fake_emro` during planning. **The Java side conforms to the existing Python client, never the reverse.** Any true conflict is resolved by an explicit documented contract note, not silently.

### 4.2 Facade 2 — PRD batch surface (`api/batch/`)

- `POST /api/v1/stock-levels` — body `{runId, transactionId, items: [...]}` (explicit canonical fields; the ARMAC string-replace hack is dropped — FR-3).
- Item fields: `rowId, partNo, location, reorderLevel, eoqLevel, stockMin, stockMax, orderMin, orderMax, replenishmentLeadTime` (**re-added** per PRD §5.1; ARMAC dropped it while the entity supports it) + optional provenance `source, approver, tier`.
- Response: HTTP 200 envelope with per-row `{rowId, status, code, message}`; `status ∈ ACCEPTED · REJECTED_VALIDATION · REJECTED_UNKNOWN_KEY · SKIPPED_DUPLICATE · ERROR`.
- `GET /api/v1/health` — SmallRye health, liveness + readiness (DB).

### 4.3 Kafka (`ingest/`)

| Topic | Direction | Payload |
|---|---|---|
| `optimizer.writeback.v1` | in | The same canonical batch JSON as Facade 2 (one schema, two transports; versioned per PRD risk table) |
| `optimizer.writeback.results.v1` | out | The same per-row results envelope, keyed by `runId` |
| `optimizer.writeback.dlq.v1` | out | Poison messages after bounded retry (3 attempts, exponential backoff) + WARN log + metric (FR-11) |

Consumer path = identical domain path as REST. Delivery at-least-once; ledger makes it effectively-once.

## 5. Domain rules (ported from ARMAC `StockLevelData`; each is a named test first)

| Rule | Source | Notes |
|---|---|---|
| PN must exist in `PN_MASTER` and be `ACTIVE` | ARMAC | reject `REJECTED_UNKNOWN_KEY` / `REJECTED_VALIDATION` |
| Location must exist in `LOCATION_MASTER`, `inventory=Y`, not quarantine | ARMAC | |
| All numeric fields ≥ 0 | ARMAC `checkInput` | |
| `min ≤ max` sanity (stock and order) | **New** (FR-5) | ARMAC lacks this |
| Category-aware numerics: consumable (`SystemTranCode` `PNCATEGORY` → `"C"`) keeps decimals; others truncate to whole units | ARMAC | applies to ROP, EOQ, min/max stock, min/max order |
| Upsert by `em.merge` on `PnInventoryLevelPK{pn, location}`; create if absent | ARMAC | |
| Audit row (`PN_INVENTORY_LEVEL_AUDIT`) on **every** change | ARMAC | |
| Company from `ProfileMaster`, default `"TRAX"` on lookup failure | ARMAC | single-profile assumption verified in smoke test |
| `createdBy` / `modifiedBy` = **authenticated caller principal + runId** | **Changed** | ARMAC hardcodes `"TRAX_IFACE"` |
| `replenishmentLeadTime` written when supplied | **Restored** | entity supports it; ARMAC DTO dropped it |

Known ARMAC bugs explicitly **not** ported: the legacy PTC `UpdateStockLevel(single)` `setCompany(sql)` bug; the `transactionId` JSON hack; `getSingleResult()` company lookup remains but its failure mode (multi-profile) is covered by a test + the `"TRAX"` default.

## 6. Auth (FR-9)

- Quarkus OIDC bearer-JWT on **every** endpoint; only health probes are anonymous.
- Roles: `writeback:write` (both facades' write paths), `writeback:read` (history).
- Tests: dev issuer (smallrye-jwt test keys, locally signed tokens). Production issuer/audience: `application.properties` only — no code change.
- Kafka channel security (SASL/mTLS): deployment configuration, not code.
- 401/403 from the framework; never hand-rolled.

## 7. Error handling

- **Per-row isolation** (§3.4): failing items yield `REJECTED_*`/`ERROR` results; the batch continues. Batch-level HTTP failures are only 400 (unparseable body) and 401/403 (auth).
- **Duplicates are success-shaped** (`SKIPPED_DUPLICATE`), never errors — retries are normal under at-least-once delivery.
- **Unexpected exceptions**: item transaction rolls back (ledger row included), logged with `runId`/`rowId` correlation, counted in metrics. No silent drops (PRD success metric #1).
- **Kafka poison**: 3 retries w/ exponential backoff → DLQ + WARN + gauge.

## 8. Observability (NFRs)

Micrometer counters (`accepted/rejected/skipped/error`, tagged by facade), batch latency timers, DLQ depth gauge; OpenTelemetry traces facade→domain→JDBC; structured JSON logs, PII-free, correlated by `runId`/`rowId`; SmallRye health with DB readiness.

## 9. Testing

1. **Domain unit tests** — every §5 rule as a named, framework-free test.
2. **`@QuarkusTest` integration** — Oracle testcontainer (`gvenzl/oracle-free`, entity-generated DDL) + Kafka Dev Services (Redpanda): REST→DB and topic→DB→results-topic full paths; JWT enforced; idempotency proven under concurrent replay of the same `(runId, rowId)`.
3. **Contract fidelity** — the `fake_emro` contract behaviors re-pinned as Java tests against Facade 1; cross-language proof = Python `RestWritebackClient` smoke against a running instance (planning task).
4. **Opt-in eMRO smoke** — env-gated profile against local `oracle19c` (real eMRO schema; dedicated test PN/location; container never managed by this project). Validates real constraints/triggers pre-deploy; also verifies the single-`ProfileMaster` assumption (D7).
5. **Load test to NFRs** (≥5k items/min; 50k rows <15 min) — hardening slice; slice-1 design choices (per-item tx + ledger) are measured against it from day one.

## 10. Explicitly out of scope (slice 1)

Rollback endpoint (slice 2) · requisitions & transfers (own slices on the same spine; PRD Phases 2–3) · replay tooling (FR-10; ledger schema supports it) · PTC LDAP/license port · inventory transaction-history posting (`PnInventoryData.InsertInventoryHistory`; PRD open Q5 remains open) · GraalVM native build · admin/status UI · production broker/IdP/platform selection (PRD open Q2/Q4 — deployment config, not code).

## 11. Repo bookkeeping (part of slice 1)

- `ROADMAP.md` — sub-project #6 gains the real-eMRO Java track with these milestones.
- Root `CLAUDE.md` — add `services/emro-writeback-java` build/test commands (Maven, `@QuarkusTest`, smoke profile).
- ADR — record the "one domain core, two facades; Java conforms to the Python client" contract decision.
