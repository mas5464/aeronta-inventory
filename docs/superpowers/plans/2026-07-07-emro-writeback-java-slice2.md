# eMRO Write-Back Service (Java) — Slice 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete `services/emro-writeback-java/`: rollback (Trax IO contract), out-of-band history, requisitions, transfers, Kafka domain routing, exception taxonomy, thin replay, and the ledger/hardening carry-forwards — per the approved [slice-2 spec](../specs/2026-07-07-emro-writeback-java-slice2-design.md) (D9–D17).

**Architecture:** Everything rides the slice-1 spine (one ledger-backed effectively-once discipline, per-item `REQUIRES_NEW`, thin facades over domain classes). Two new domain creators (`RequisitionCreator`, `TransferCreator`) mirror `StockLevelWriter`'s transactional shape; `RollbackService` reuses the writer's write path; eMRO sequence packages sit behind seams so Dev Services tests stay hermetic.

**Tech Stack:** unchanged from slice 1 — Quarkus 3.22.x, Java 21, Hibernate ORM, Oracle + Kafka Dev Services, Flyway (service schema only), SmallRye JWT, Micrometer, JUnit 5.

**Branch:** `claude/emro-writeback-slice-2` (stacked on slice 1 / PR #4 at `ab98590`). Baseline: **70/70 tests green.**

## Global Constraints (inherited from slice 1 + spec D9–D17)

- Never DDL eMRO tables in any profile; Flyway owns ONLY `WRITEBACK_LEDGER`. **V1 is amended in place** (still pre-deployment) — no V2 migration files.
- Never manage the `oracle19c` container; smoke tests are env-gated, connect-only, DML-restoring.
- JWT on every endpoint except `/q/health*`. Roles: `writeback:write` (apply/rollback/creates), `writeback:read` (history, out-of-band, replay).
- Facades/consumers call ONLY the domain classes' non-transactional dedup wrappers; per-row isolation; ERROR wire-messages sanitized (log raw, respond generic); batch endpoints always HTTP 200 with per-row results (400 unparseable / 401 / 403 only).
- Trax IO facade wire = snake_case via explicit `@JsonProperty` per field; batch facades = camelCase. The Java side conforms to the Python contract (`services/agent-spine/src/trax_io_spine/writeback/contracts.py`), never the reverse.
- Ledger uniqueness is composite `(TENANT_ID, IDEMPOTENCY_KEY)`; all ledger lookups tenant-filtered; duplicates are success-shaped and replay the original outcome (incl. `CREATED_REF` for creates); rejected rows are never ledgered.
- Tests: TDD; committed-state assertions via `QuarkusTransaction.requiringNew()`; namespace seed keys per test class; full suite green before every commit (`mvn test -Dnet.bytebuddy.experimental=true` — the flag is CLI-only, never committed config). Foreground runs, timeout 600000ms, no background tasks in implementers.
- Conventional commits; only files under `services/emro-writeback-java/` except the bookkeeping task.
- ARMAC reference source (read-only, outside repo): `/Users/miguelsosa/trax-mgmt-armac_interfaces/`. Entity lifts use slice 1's four mechanical changes: package → `trax.io.writeback.persistence`; delete relationship fields; make PK `@Column`s writable; keep Lombok + every `@Column` name verbatim.
- Existing slice-1 files are the pattern library — implementers should read `StockLevelWriter.java`, `BatchProcessor.java`/`BatchResource.java`, `TraxIoResource.java`, `TraxIoHistoryResource.java`, `WritebackConsumer.java`, and their tests before writing anything.

---

### Task 1: Ledger extension — DOMAIN, CREATED_REF, MESSAGE

**Files:**
- Modify: `services/emro-writeback-java/src/main/resources/db/migration/V1__writeback_ledger.sql`
- Modify: `.../persistence/WritebackLedger.java`
- Modify: `.../domain/StockLevelWriter.java` (populate `MESSAGE` + `DOMAIN`)
- Modify: `.../api/traxio/TraxIoHistoryResource.java` (non-null `new_values` guarantee comment + defensive check)
- Test: extend `.../persistence/FlywayMigrationTest.java`, `.../domain/StockLevelWriterTest.java`

**Interfaces:**
- Produces: `WritebackLedger` gains `String domain` (`DOMAIN VARCHAR2(16) NOT NULL`, values `STOCK_LEVEL|REQUISITION|TRANSFER`), `String createdRef` (`CREATED_REF VARCHAR2(64)` nullable), and `setMessage(...)` is now populated. Constant `StockLevelWriter.DOMAIN_STOCK_LEVEL = "STOCK_LEVEL"`. Later creator tasks set `DOMAIN=REQUISITION|TRANSFER` + `CREATED_REF=<req/order number>`.

- [ ] **Step 1 (failing tests):** In `FlywayMigrationTest`: extend the JDBC insert helper with `DOMAIN` (add the column, value `'STOCK_LEVEL'`) and assert (a) inserting a row with NULL `DOMAIN` throws (NOT NULL), (b) `CREATED_REF` column exists (metadata check). In `StockLevelWriterTest`: `ledger_row_carries_domain_and_message` — a successful write's ledger row has `DOMAIN='STOCK_LEVEL'` and non-null `MESSAGE` (equal to the ItemResult message, e.g. "accepted"), read back via committed native query.
- [ ] **Step 2:** Run to fail: `mvn test -Dtest='FlywayMigrationTest,StockLevelWriterTest' -Dnet.bytebuddy.experimental=true` (existing helpers don't set DOMAIN → NOT NULL violation once SQL is updated; write SQL first, then watch the old tests fail, then fix).
- [ ] **Step 3:** Amend V1 SQL: add `DOMAIN VARCHAR2(16) NOT NULL` and `CREATED_REF VARCHAR2(64)` after `OUTCOME`. Mirror both on the entity (`@Column(name="DOMAIN", nullable=false)`, `@Column(name="CREATED_REF")`). In `StockLevelWriter.writeItem`, set `ledger.setDomain(DOMAIN_STOCK_LEVEL)` and `ledger.setMessage(<the same message the ItemResult carries>)`. In `TraxIoHistoryResource`, add the `new_values`-non-null guarantee: a comment stating every ledgered row has non-null `NEW_VALUES_JSON` by construction, plus a defensive `Objects.requireNonNull(parsed, "ledger row " + id + " has null new_values")`-style check.
- [ ] **Step 4:** Focused green, then FULL suite green (existing StockLevelWriter/Flyway/history tests must all still pass — every existing test-side ledger insert needs the DOMAIN column added).
- [ ] **Step 5:** Commit `feat(writeback-java): ledger DOMAIN/CREATED_REF columns + populated MESSAGE`.

---

### Task 2: Rollback — domain service + Trax IO endpoint (contract-exact)

**Files:**
- Create: `.../domain/RollbackService.java`
- Modify: `.../api/traxio/TraxIoDtos.java` (+`RollbackRequestDto`, `RollbackResultDto`), `.../api/traxio/TraxIoResource.java` (or a sibling `TraxIoRollbackResource` if path structure demands — same package)
- Test: `.../api/traxio/TraxIoRollbackTest.java`

**Interfaces:**
- Consumes: `StockLevelWriter.writeItemDedup(WritebackCommand)` (the reverting write goes through the normal write path), `StockLevelWriter.history(...)`, ledger lookups.
- Produces: `RollbackService.rollback(RollbackCommand cmd) -> RollbackOutcome` where `RollbackCommand(String tenantId, String pn, String location, String reason, String principal, Instant requestedAt)` and `RollbackOutcome(Status status, Map<String,Integer> fromValues, Map<String,Integer> toValues, Long revertedFromVersion, Long newVersion, Instant rolledBackAt, String errorMessage)`, `Status ∈ ROLLED_BACK, OUTSIDE_WINDOW, NOTHING_TO_REVERT`. Window: config property `writeback.rollback.window-days` (default 90; reject 0/negative at startup with a config validation).

**Wire contract (authoritative — from `contracts.py`, conform exactly):**
- `POST /traxio/v1/rollback` (`writeback:write`), request: `{"tenant_id","pn","location","reason","principal"("planner" default),"requested_at"}`.
- Response 200 always (even for OUTSIDE_WINDOW/NOTHING_TO_REVERT — fake_emro returns 200 with the status in the body): `{"tenant_id","pn","location","status":"rolled_back"|"outside_window"|"nothing_to_revert","from_values","to_values","reverted_from_version","new_version","rolled_back_at","error_message"}`.

**Semantics (each is a named test — mirror agent-spine `tests/writeback/test_rollback.py`):**
1. `rollback_reverts_latest_written_to_prior_values` — latest `WRITTEN` ledger entry with non-null `old_values`: apply those old_values as a NEW write through `writeItemDedup` (idempotency key `"rollback:" + <reverted entry's idempotency_key>`, provenance_id `"rollback:" + <reverted entry's provenance_id>`, principal from request, tier null, shadow false). Outcome: `ROLLED_BACK`, `from_values` = reverted entry's new_values, `to_values` = its old_values, `reverted_from_version` = its version, `new_version` = the new ledger row's version.
2. `rollback_with_no_prior_write_is_nothing_to_revert`.
3. `rollback_of_only_first_write_is_nothing_to_revert` (old_values null → nothing to revert to).
4. `rollback_outside_window` — `requested_at - <entry's created_at> > window` → `OUTSIDE_WINDOW`, **no mutation** (assert level row + ledger count unchanged).
5. `subsequent_write_sees_rolled_back_values_as_old_values`.
6. `shadowed_entries_are_skipped_when_finding_latest_written` (a shadow after a write: rollback reverts the WRITTEN one).
Plus facade tests: snake_case fidelity (raw-JSON body, JsonPath key assertions incl. `rolled_back_at`), 401/403.

- [ ] Step 1 failing tests (raw JSON bodies) → Step 2 run to fail → Step 3 implement (`RollbackService` finds the latest WRITTEN row with non-null old_values via a tenant-filtered ledger query ordered by version desc; window check; delegate the write; map to DTO) → Step 4 focused + FULL green → Step 5 commit `feat(writeback-java): rollback endpoint conforming to agent-spine contract`.

---

### Task 3: Out-of-band history endpoint

**Files:**
- Create: `.../api/traxio/OutOfBandHistoryResource.java` (+DTO in `TraxIoDtos.java`)
- Test: `.../api/traxio/OutOfBandHistoryTest.java`

**Interfaces:**
- Produces: `GET /traxio/v1/history/out-of-band?tenant_id=&pn=&location=` (`writeback:read`) → JSON array of `{"pn","location","modified_by","modified_date","reorder_level","eoq_level","minimum_stock","maximum_stock","minimum_order","maximum_order"}` — rows from `PN_INVENTORY_LEVEL_AUDIT` whose `MODIFIED_BY` is NOT one of this service's writing principals. "This service's principals" = the set of `PRINCIPAL` values present in `WRITEBACK_LEDGER` for that `(tenant, pn, location)` (data-driven, no hardcoded principal list). No `version` field — deliberately NOT the `HistoryEntry` shape (D13).
- Note: `tenant_id` is accepted for interface symmetry but the audit table has no tenant column (single-tenant eMRO DB) — document that in the resource Javadoc.

- [ ] Step 1 failing tests: `out_of_band_edit_appears` (seed an audit row via native insert with `MODIFIED_BY='SOMEONE_ELSE'` → returned), `service_writes_are_excluded` (apply a real write via the Trax IO endpoint → its audit row is NOT in out-of-band), `read_role_required` (403 for write-only token). → Step 2 fail → Step 3 implement (JPQL/native over the audit entity, `NOT IN` the ledger-principals subquery) → Step 4 focused + FULL green → Step 5 commit `feat(writeback-java): out-of-band history endpoint (audit-backed, contract-safe)`.

---

### Task 4: Requisition entities + number-source seams

**Files:**
- Create (lift from `/Users/miguelsosa/trax-mgmt-armac_interfaces/TraxReorderRequisition/src/main/java/trax/aero/model/`): `.../persistence/RequisitionHeader.java`, `RequisitionDetail.java` (+ their `*Audit`/`*PK` classes as present in that directory — apply slice 1's four mechanical changes)
- Create: `.../persistence/RequisitionNumberSource.java` (interface: `String nextRequisitionNumber()`), `.../persistence/EmroRequisitionNumberSource.java` (`@ApplicationScoped @DefaultBean`… see step 3), `OrderNumberSource.java` (interface: `String nextOrderNumber()`), `EmroOrderNumberSource.java`
- Test: `.../persistence/NumberSourceTest.java`

**Interfaces:**
- Produces: the lifted requisition entities; `RequisitionNumberSource.nextRequisitionNumber()` and `OrderNumberSource.nextOrderNumber()` CDI beans. Real impls run the eMRO package calls ARMAC uses (`SELECT PKG_APPLICATION_FUNCTION.config_number('REQSEQ') FROM dual` — read `RequisitionData.java` for the verbatim query; the transfers analog uses the `POSEQ` config — read `StockTransferOrderData.getTransactionNo`). Test profile provides sequence-backed impls: `%test` alternative beans (`@Alternative @Priority` or `@IfBuildProfile("test")`) that `CREATE SEQUENCE`-…no — **no DDL**: back the test impls with an `AtomicLong` prefix� formatted (`"RTEST-%06d"` / `"TTEST-%06d"`) — uniqueness within a test JVM is sufficient.
- Both eMRO impls must be excluded from `%test` CDI resolution (use `@DefaultBean` from `io.quarkus.arc` on the real ones and `@Alternative @Priority(1) @ApplicationScoped` test beans under `src/test/java`, or `@IfBuildProfile`). Document the choice in Javadoc.

- [ ] Step 1 failing test: `test_number_sources_produce_unique_monotonic_refs` (inject both sources in `@QuarkusTest`; two calls each → distinct, non-blank). Entities: compile-level proof + a persistence round-trip test for `RequisitionHeader`+`Detail` (native insert via Hibernate-generated tables, read back). → Step 2 fail → Step 3 lift entities + implement seams → Step 4 focused + FULL green → Step 5 commit `feat(writeback-java): requisition entities + eMRO number-source seams`.

---

### Task 5: RequisitionCreator domain

**Files:**
- Create: `.../domain/RequisitionCreator.java`
- Test: `.../domain/RequisitionCreatorTest.java`

**Interfaces:**
- Consumes: `TraxRepository` (PN/location validation, `isConsumable`, `company()`), `RequisitionNumberSource`, `NumericPolicy`, ledger (Task 1 columns), `Provenance`.
- Produces: `RequisitionCreator.createDedup(RequisitionCommand cmd) -> RequisitionResult` (non-transactional dedup wrapper over `@Transactional(REQUIRES_NEW) create(...)`, mirroring `writeItemDedup`'s classification/retry/ground-truth-refetch structure — read `StockLevelWriter` first). `RequisitionCommand(String pn, String location, BigDecimal qty, LocalDate needBy, String remarks, Provenance provenance)`. `RequisitionResult(ResultStatus status, int code, String message, Long rowId, String requisition, Integer line)`.

**Business rules (port from ARMAC `/Users/miguelsosa/trax-mgmt-armac_interfaces/TraxReorderRequisition/src/main/java/trax/aero/data/RequisitionData.java` — read it; each rule = a named test):**
1. `unknown_or_inactive_pn_rejected`, `ineligible_location_rejected` (reuse `TraxRepository` checks; same REJECTED_* statuses as slice 1).
2. `non_positive_qty_rejected` (qty ≤ 0 → REJECTED_VALIDATION).
3. `qty_is_category_aware` (consumable keeps decimals; else truncated — `NumericPolicy`).
4. `creates_header_and_detail_with_audits` — header: number from `RequisitionNumberSource`, the ARMAC field set (read the source: type/priority `REOR`, status `OPEN`, company from `repo.company()`, created/modified stamps = provenance principal + now); detail: line 1, pn/location/qty. Audit rows mirror both (audit PKs need the same created-by/date/company discipline as slice 1's audit — check the lifted audit PK fields).
5. `ledger_row_domain_requisition_with_created_ref` — outcome WRITTEN, `DOMAIN='REQUISITION'`, `CREATED_REF=<req number>`, MESSAGE populated.
6. `duplicate_returns_skipped_with_original_requisition_number` — replay carries the original `CREATED_REF` back in `RequisitionResult.requisition`.
7. `concurrent_same_key_creates_exactly_one_requisition` (two threads, one ACCEPTED + one SKIPPED_DUPLICATE, exactly one header row).
Note the PnInterchangeable resolution ARMAC does: **out of scope** — document in Javadoc (the optimizer sends resolved PNs; slice-1 writer doesn't resolve either; consistency).

- [ ] Steps: failing tests → fail → implement → focused + FULL green → commit `feat(writeback-java): RequisitionCreator — validated, ledgered, effectively-once`.

---

### Task 6: Requisitions REST facade

**Files:**
- Create: `.../api/batch/RequisitionDtos.java`, `.../api/batch/RequisitionResource.java`, `.../api/batch/RequisitionProcessor.java`
- Test: `.../api/batch/RequisitionResourceTest.java`

**Interfaces:**
- Consumes: `RequisitionCreator.createDedup`.
- Produces: `POST /api/v1/requisitions` (`writeback:write`), camelCase: request `{runId, transactionId, tenantId?, items:[{rowId, partNo, location, qty, needBy?, remarks?, source?, tier?, approver?}]}` → 200 `{runId, transactionId, results:[{rowId, status, code, message, requisition, line}]}`. `RequisitionProcessor.process(RequisitionBatchRequest, String tenantId, String principal, String facadeTag) -> RequisitionBatchResponse` (JAX-RS-free — Kafka reuses it in Task 9). Tenant from JWT claim (REST) — body `tenantId` is Kafka-only, same as slice 1. Metrics: increment the existing `writeback.items` counter with `facade` tag; ERROR sanitization identical to `BatchProcessor.toRowResult` (read it).

- [ ] Steps: failing tests (`no_token_401`, `wrong_role_403`, `mixed_batch_per_row` incl. a real create asserting the header row exists + `requisition` echoed, `replay_returns_original_requisition`, `null_items_200_empty`) → fail → implement → focused + FULL green → commit `feat(writeback-java): requisitions batch facade`.

---

### Task 7: Transfer entities + TransferCreator domain

**Files:**
- Create (lift from `/Users/miguelsosa/trax-mgmt-armac_interfaces/StockTransferOrderService/src/main/java/trax/aero/model/`): `.../persistence/OrderHeader.java`, `OrderDetail.java`, `OrderHeaderAudit.java`, `OrderDetailAudit.java` (+ PKs) — four mechanical changes
- Create: `.../domain/TransferCreator.java`
- Test: `.../domain/TransferCreatorTest.java`

**Interfaces:**
- Consumes: `TraxRepository`, `OrderNumberSource` (Task 4), ledger, `NumericPolicy`.
- Produces: `TransferCreator.createDedup(TransferCommand cmd) -> TransferResult`. `TransferCommand(String pn, String fromLocation, String toLocation, BigDecimal qty, String batch, LocalDate deliveryDate, Provenance provenance)`. `TransferResult(ResultStatus status, int code, String message, Long rowId, String orderNumber, String batch)`.

**Business rules (port from ARMAC `.../StockTransferOrderService/src/main/java/trax/aero/data/StockTransferOrderData.java` — read `checkMinData` + `createOrderHeader`; each = named test):**
1. `unknown_pn_rejected`, `ineligible_from_location_rejected`, `ineligible_to_location_rejected`, `same_from_and_to_rejected`, `non_positive_qty_rejected`.
2. `creates_ts_order_header_and_detail_with_audits` — `ORDER_HEADER` PK `{orderType="TS", orderNumber=<OrderNumberSource>}`, detail line 1 with pn/qty/from/to per the ARMAC field mapping (read the source for exact setters), audits mirrored.
3. `qty_is_category_aware`.
4. `ledger_row_domain_transfer_with_created_ref` (CREATED_REF = order number; MESSAGE populated).
5. `duplicate_returns_skipped_with_original_order_number`.
6. `concurrent_same_key_creates_exactly_one_order`.

- [ ] Steps: failing tests → fail → implement (lift entities first, compile, then creator) → focused + FULL green → commit `feat(writeback-java): transfer entities + TransferCreator`.

---

### Task 8: Transfers REST facade

**Files:**
- Create: `.../api/batch/TransferDtos.java`, `TransferResource.java`, `TransferProcessor.java`
- Test: `.../api/batch/TransferResourceTest.java`

**Interfaces:**
- Consumes: `TransferCreator.createDedup`.
- Produces: `POST /api/v1/transfers` (`writeback:write`), camelCase: `{runId, transactionId, tenantId?, items:[{rowId, partNo, fromLocation, toLocation, qty, batch?, deliveryDate?}]}` → 200 `{runId, transactionId, results:[{rowId, status, code, message, orderNumber, batch}]}`. `TransferProcessor.process(TransferBatchRequest, tenantId, principal, facadeTag)` JAX-RS-free. Same JWT/tenant/metrics/sanitization rules as Task 6.

- [ ] Steps: failing tests (same five shapes as Task 6, transfer-flavored) → fail → implement → focused + FULL green → commit `feat(writeback-java): transfers batch facade`.

---

### Task 9: Kafka domain discriminator

**Files:**
- Modify: `.../ingest/WritebackConsumer.java`, `.../api/batch/BatchDtos.java` (or a small `.../ingest/ActionEnvelope.java`)
- Test: extend `.../ingest/WritebackConsumerTest.java`

**Interfaces:**
- Consumes: `BatchProcessor.process`, `RequisitionProcessor.process`, `TransferProcessor.process` (Tasks 6/8).
- Produces: the consumer peeks an optional top-level `"domain"` field on the incoming JSON (`"stock_level"` default | `"requisition"` | `"transfer"`) and routes to the matching processor with `facadeTag="kafka"`, principal `"kafka-ingest"`, tenant from body-or-default; unknown `domain` value → DLQ (poison, verbatim, keyed null). Results envelope: serialize the matching response type; record key = runId (existing behavior).

- [ ] Step 1 failing tests: `requisition_message_routes_and_creates` (produce `{"domain":"requisition",...}` → header row + results record with `requisition` field), `transfer_message_routes` (analog), `missing_domain_defaults_to_stock_level` (existing behavior pinned), `unknown_domain_goes_to_dlq_verbatim`. → Step 2 fail → Step 3 implement (parse to `JsonNode` first for the peek, then bind to the right DTO) → Step 4 focused + FULL green → Step 5 commit `feat(writeback-java): Kafka domain discriminator routing`.

---

### Task 10: Exception taxonomy + emitter/audit hardening

**Files:**
- Create: `.../domain/InfrastructureException.java`
- Modify: `.../domain/StockLevelWriter.java` (+ the two creators): classify + rethrow; audit-PK retry (D17)
- Modify: `.../api/batch/BatchProcessor.java` (+ Requisition/Transfer processors): catch `InfrastructureException` → per-row ERROR (REST contract preserved) **unless** invoked with `failFast=true`
- Modify: `.../ingest/WritebackConsumer.java`: pass `failFast=true` so `InfrastructureException` propagates into the existing retry→DLQ loop; results-emitter bounded retry; `@OnOverflow(BUFFER)` config
- Test: `.../domain/ExceptionTaxonomyTest.java`, extend `WritebackConsumerTest`

**Interfaces:**
- Produces: `InfrastructureException extends RuntimeException` thrown by domain dedup wrappers ONLY for connection-class failures: the cause chain contains `SQLTransientException`, `SQLNonTransientConnectionException`, `SQLRecoverableException`, or Agroal acquisition failure — never constraint violations, never validation. Processors gain a `boolean failFast` parameter (existing signatures delegate with `false`).
- D17: in the writer's classification, an ORA-00001 naming `PN_INVENTORY_LEVEL_AUDIT` → new `AUDIT_PK_COLLISION` classification → bounded retry (2 extra attempts, backoff ≥1100ms so second-precision `CREATED_DATE` advances) — replaces the slice-1 audit-collision→ERROR fail-safe with a self-healing retry, exhaustion still → ERROR. (Unit-test the classifier branch; the live collision is untestable on the TIMESTAMP test schema — document.)
- Kafka results emitter: wrap `results.send(...)` in try/catch — 3 attempts with 200ms backoff; final failure → send the RESPONSE JSON to the DLQ (keyed runId) + WARN, then ACK (no more infinite redelivery). Config: `mp.messaging.outgoing.writeback-results.overflow-strategy`… use `@OnOverflow(value = OnOverflow.Strategy.BUFFER, bufferSize = 1024)` on both emitters (documented).
- Test: a `@Alternative` test bean or injectable failure hook forcing `InfrastructureException` from a processor → Kafka path: message retried 3× then DLQ'd (assert DLQ record); REST path: per-row ERROR 200 (assert unchanged contract).

- [ ] Steps: failing tests → fail → implement → focused + FULL green → commit `feat(writeback-java): infrastructure exception taxonomy, reachable Kafka retry, emitter + audit-PK hardening`.

---

### Task 11: Replay endpoint + README

**Files:**
- Create: `.../api/batch/RunResultsResource.java` (+DTO)
- Modify: `services/emro-writeback-java/README.md`
- Test: `.../api/batch/RunResultsResourceTest.java`

**Interfaces:**
- Produces: `GET /api/v1/runs/{runId}/results` (`writeback:read`) → 200 `{runId, results:[{rowId, domain, pn, location, outcome, createdRef, version, message, createdAt}]}` from tenant-filtered ledger rows where `RUN_ID = :runId` (tenant from JWT claim), ordered by `rowId`. Empty run → `{runId, results: []}`. README: new "Replay" section documenting this endpoint + the full re-drive path (Kafka retention + consumer-group offset reset + idempotent consumer) + all new endpoints from this slice.

- [ ] Steps: failing tests (`results_reflect_ledgered_outcomes` — run a 2-item batch then GET, assert both rows with domains/refs; `empty_run_returns_empty`; `read_role_required`) → fail → implement → focused + FULL green → commit `feat(writeback-java): run-results replay endpoint + README`.

---

### Task 12: Smoke extension + bookkeeping

**Files:**
- Modify: `.../smoke/EmroSchemaSmokeTest.java` (package-existence checks: `SELECT PKG_APPLICATION_FUNCTION.config_number('REQSEQ') FROM dual` wrapped read-only — it INCREMENTS a sequence config; instead check existence via `SELECT COUNT(*) FROM ALL_OBJECTS WHERE OBJECT_NAME='PKG_APPLICATION_FUNCTION' AND STATUS='VALID'` and the `REQUISITION_HEADER`/`ORDER_HEADER` table/column visibility via ROWNUM=1 metadata — NO document creation)
- Modify: `ROADMAP.md` (#6 slice-2 checkboxes → checked, with date + head commit), `TASKS.md` (new dated entry), the roadmap **Future ADRs** list (0016–0020 → 0017–0021)
- Create: `docs/adr/2026-07-07-0016-emro-writeback-slice2.md` (records D9–D17 + any deviations discovered during this slice; supplements ADR-0015; read ADR-0015 for style)
- Test: full suite

**Steps:**
- [ ] Extend the smoke test (metadata/read-only only — no sequence consumption, no DML on requisition/order tables) + verify default `mvn test` still excludes it.
- [ ] ADR-0016; ROADMAP #6 slice-2 items checked + future-ADR renumber; TASKS.md entry (summarize tasks, catches, tests count).
- [ ] FULL suite green. Commit `docs: writeback-java slice 2 bookkeeping — ADR-0016, ROADMAP, TASKS, smoke extension` (this task alone may touch files outside the module).

---

## Self-Review (performed at write time)

1. **Spec coverage:** D9 (stacked branch — pre-existing) ✓; D10→T1 ✓; D11→T4 ✓; D12→T2 (all six pinned behaviors + window config) ✓; D13→T3 ✓; D14→T9 ✓; D15→T10 ✓; D16→T11 ✓; D17→T10 ✓; §5 hardening table → T1 (MESSAGE, new_values), T10 (taxonomy, emitter loop, overflow, audit-PK) ✓; §6 testing discipline in Global Constraints ✓; §8 bookkeeping→T12 ✓. Requisitions §3/§4.2→T4-T6; transfers→T7-T8 ✓.
2. **Placeholder scan:** none ("read the ARMAC source for exact field mapping" references a concrete, existing artifact at an exact path — the slice-1-proven lift discipline; slice-1 pattern references point at committed code).
3. **Type consistency:** `createDedup` naming shared by both creators; `ResultStatus` reused from slice 1; processor signatures carry `(request, tenantId, principal, facadeTag)` and Task 10 adds `failFast` with delegating overloads; `Provenance` unchanged (composite tenant uniqueness landed in slice 1's PR fix). `RequisitionResult.requisition`/`TransferResult.orderNumber` match the facade DTO fields in T6/T8 and the Kafka results in T9.

**Execution order:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 (strict; 2/3 could swap with 4-8 but keep serial per SDD).
