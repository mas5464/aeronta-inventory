# eMRO Write-Back Service (Java) — Slice 2 Design: Everything Remaining

**Date:** 2026-07-07
**Status:** Draft (awaiting user review)
**Builds on:** [Slice 1 spec](2026-07-06-emro-writeback-java-slice1-design.md) (D1–D8 stand unchanged) · [ADR-0015](../../adr/2026-07-07-0015-emro-writeback-java-service.md) · slice-1 code at `services/emro-writeback-java/` (65 tests green, PR #4)
**Branch:** `claude/emro-writeback-slice-2`, stacked on slice 1's `claude/nervous-swirles-424ddf` (PR #4). One PR for slice 2 targeting the slice-1 branch (or main after #4 merges).
**Scope decision (user, 2026-07-07):** *everything remaining* — the full completion of the PRD (Phases 2–4) plus the Trax IO #6 seam (rollback, history supplement) and the slice-1 carry-forwards.

---

## 1. Summary

Slice 2 completes the write-back service:

1. **Rollback** (`POST /traxio/v1/rollback`) — the last missing piece of the Python `RestWritebackClient` contract.
2. **Audit-supplement history** — out-of-band eMRO edits become visible without polluting the contract endpoint.
3. **Requisitions** (PRD Phase 2) — `REQUISITION_HEADER/_DETAIL` creation, ported from ARMAC `TraxReorderRequisition`.
4. **Transfers** (PRD Phase 3) — `ORDER_HEADER/_DETAIL` (type `TS`) creation, ported from ARMAC `StockTransferOrderService`.
5. **Hardening** (PRD Phase 4 subset + slice-1 carry-forwards) — exception taxonomy (Kafka infra-retry becomes reachable), thin replay, ledger `MESSAGE`/`DOMAIN`/`CREATED_REF`, audit-PK collision mitigation, results-emitter backoff.

Everything rides the slice-1 spine: same module, same ledger-backed effectively-once discipline, same JWT posture, same one-core-many-facades shape — now with three domains behind a common `DomainWriter` seam.

## 2. New decisions (D9–D17; D1–D8 unchanged from slice 1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D9 | Slicing | One slice, one stacked PR | User chose "everything remaining"; branch stacked so PR #4 stays clean |
| D10 | Ledger extension | Amend **V1 in place** (still pre-deployment): add `DOMAIN VARCHAR2(16) NOT NULL` (`STOCK_LEVEL`/`REQUISITION`/`TRANSFER`), `CREATED_REF VARCHAR2(64)` (requisition/order number for replay + duplicate-replay responses); **populate `MESSAGE`** with the per-row outcome message (closes the dead-column minor) | One ledger, one idempotency rule for all three domains; duplicates of a requisition/transfer replay the original `CREATED_REF` |
| D11 | eMRO number sources | Behind seams: `RequisitionNumberSource` / `OrderNumberSource`. Real impls call the eMRO packages ARMAC uses (`PKG_APPLICATION_FUNCTION.config_number('REQSEQ')`, `getTransactionNo("POSEQ")` semantics) via native query; test impls use plain DB sequences (the eMRO packages don't exist in the Dev Services schema). Smoke test gains package-existence checks | Same trick as `fake_emro`: contract-tested logic, real dependency isolated at a seam |
| D12 | Rollback semantics | Per the extracted Python contract, matching `fake_emro` behavior test-for-test: revert the latest `WRITTEN` entry with non-null `old_values` by applying them as a **new write** (new version; `parent_version` → the reverted version; provenance `"rollback:{provenance_id}"`; principal from request, default `"planner"`). `NOTHING_TO_REVERT` when no writes or only a first-write (null `old_values`); `OUTSIDE_WINDOW` beyond the window (default 90d, configurable, never zero) | The Java side conforms to the Python client, never the reverse (slice-1 rule) |
| D13 | Audit-supplement history | **Separate endpoint** `GET /traxio/v1/history/out-of-band` (own DTO: values, timestamps, `modified_by`, no fabricated `version`) listing `PN_INVENTORY_LEVEL_AUDIT` rows whose `MODIFIED_BY` is not one of this service's principals. The contract endpoint (`/history`) stays byte-compatible with `HistoryEntry` | Fabricating monotonic `version` ints for out-of-band edits would corrupt the contract; a supplementary endpoint is honest |
| D14 | Kafka action records (FR-1) | One topic, one consumer; the batch message gains an optional `domain` discriminator (`stock_level` default \| `requisition` \| `transfer`) routing to the matching processor. REST facades stay separate per domain | Backward compatible (missing `domain` = slice-1 behavior); "typed action records" per the PRD without a topic explosion |
| D15 | Exception taxonomy | New unchecked `InfrastructureException` thrown by the writer/creators for connection-class failures only (`SQLTransientException`, `SQLNonTransientConnectionException`, connection-acquisition failures — NOT constraint violations, NOT validation). Kafka path: propagates → the existing 3-attempt retry → DLQ (the dead path becomes reachable, with a test via a toggleable failure hook). REST path: caught at the facade → per-row `ERROR` (slice-1 wire contract preserved, pinned tests untouched) | Closes the "infra-retry unreachable" carry-forward without breaking the REST always-200 contract |
| D16 | Replay (FR-10, thin) | `GET /api/v1/runs/{runId}/results` re-emits recorded per-row results from the ledger (`writeback:read`). Full payload re-drive stays on Kafka retention / consumer-offset reset, documented in the README. No payload archive in slice 2 | Rejected rows are deliberately not ledgered; archiving raw payloads is a data-retention decision that belongs with production deployment |
| D17 | Audit-PK collision mitigation | We cannot alter eMRO's audit PK (their schema). Classify an audit-table ORA-00001 (match `PN_INVENTORY_LEVEL_AUDIT` in the exception text) → bounded retry (2 attempts, ≥1.1s backoff so `CREATED_DATE` second-precision advances). Applies to the stock-level writer only | Converts the real-Oracle same-second collision from ERROR-500 into a self-healing retry |

## 3. Domain design

```
domain/
├─ StockLevelWriter        (slice 1, gains: D15 taxonomy, D17 audit retry, MESSAGE populate)
├─ RequisitionCreator      (new) — validate (PN active, location eligible, qty > 0, category-aware qty
│                           via NumericPolicy) → REQUISITION_HEADER + REQUISITION_DETAIL (+ audits)
│                           via RequisitionNumberSource → ledger row (DOMAIN=REQUISITION,
│                           CREATED_REF=req number) → ItemResult + {requisition, line}
├─ TransferCreator         (new) — validate (PN, from/to locations eligible + distinct, qty > 0)
│                           → ORDER_HEADER (type TS) + ORDER_DETAIL (+ audits) via OrderNumberSource
│                           → ledger row (DOMAIN=TRANSFER, CREATED_REF=order number)
│                           → ItemResult + {orderNumber, batch}
└─ RollbackService         (new) — ledger-backed per D12; the reverting write goes through
                            StockLevelWriter's upsert+audit+ledger path (one write discipline)
```

- **Idempotency for creates (FR-4):** dedupe/skip — a duplicate `idempotency_key` returns `SKIPPED_DUPLICATE` carrying the original `CREATED_REF` (no second requisition/order is ever created). Same ledger, same unique constraint, same concurrent-loser semantics as slice 1.
- Creators follow the writer's transactional shape: per-item `REQUIRES_NEW`, ledger insert in the same tx, `writeItemDedup`-style non-transactional dedup wrappers.
- **New entities (lifted from ARMAC, same 4 mechanical changes as slice 1):** `RequisitionHeader/Detail(+Audits,+PKs)` from `TraxReorderRequisition`, `OrderHeader/Detail(+Audits,+PKs)` from `StockTransferOrderService`. Slice-1 trimmed read entities are reused.

## 4. Contracts

### 4.1 Rollback (Trax IO seam — wire-exact, snake_case)

`POST /traxio/v1/rollback` (`writeback:write`) — request `{tenant_id, pn, location, reason, principal (default "planner"), requested_at}` → 200 `{tenant_id, pn, location, status: "rolled_back"|"outside_window"|"nothing_to_revert", from_values, to_values, reverted_from_version, new_version, rolled_back_at, error_message}`. Behaviors pinned by re-implementing agent-spine's `test_rollback.py` cases as Java tests (revert-latest, first-write-nothing, no-writes-nothing, outside-window-no-mutation, parent-version chaining, subsequent-write-sees-rolled-back-values).

### 4.2 Requisitions & transfers (PRD batch surface, camelCase)

- `POST /api/v1/requisitions` — `{runId, transactionId, items: [{rowId, partNo, location, qty, needBy?, remarks?, source?, tier?, approver?}]}` → per-row `{rowId, status, code, message, requisition, line}`.
- `POST /api/v1/transfers` — `{runId, transactionId, items: [{rowId, partNo, fromLocation, toLocation, qty, batch?, deliveryDate?}]}` → per-row `{rowId, status, code, message, orderNumber, batch}`.
- Both `writeback:write`, per-row isolation, ERROR sanitization, tenant from JWT claim — all slice-1 rules inherited verbatim.

### 4.3 Kafka

Same topic/channels; message gains optional `domain` (D14). Results envelope gains the domain + created refs. DLQ/retry unchanged, now genuinely reachable for infra failures (D15).

### 4.4 Replay

`GET /api/v1/runs/{runId}/results` (`writeback:read`) → the recorded per-row outcomes for that run from the ledger (domain, pn/location, status, `CREATED_REF`, versions, timestamps). README documents the full re-drive path (Kafka retention + offset reset + idempotent consumer).

## 5. Hardening items (all in slice 2)

| Item | Action |
|---|---|
| Kafka infra-retry unreachable | D15 taxonomy + a test that forces `InfrastructureException` and observes retry→DLQ |
| Results-emitter tight redelivery loop | Wrap `results.send()` failure: bounded retry + WARN; final failure → DLQ the *response* JSON (documented) rather than infinite redelivery |
| Ledger `MESSAGE` dead column | Populated for every ledger row (outcome message) — used by replay |
| `new_values` nullability | Non-null guarantee comment + defensive check in the history mapper |
| Audit-PK same-second collision | D17 bounded ≥1.1s retry |
| Emitter overflow policy | Explicit `@OnOverflow(BUFFER)` with documented capacity on both outgoing channels |

## 6. Testing

Slice-1 discipline unchanged: TDD per task; `@QuarkusTest` on Dev Services Oracle + Kafka; committed-state assertions; contract tests re-pinned from the Python side (`test_rollback.py` cases); number-source seams unit-tested, real-package checks added to the env-gated smoke test; the full slice-1 suite (65) must stay green throughout.

## 7. Out of scope (slice 2)

Production deployment (IdP, broker, K8s/OpenShift, Oracle account) · payload archive for offline re-drive (D16 rationale) · inventory transaction-history posting (`InsertInventoryHistory`, PRD open Q5 — still open) · PTC LDAP · GraalVM native · load test to NFR numbers (needs prod-like infra; the design is measured against it) · admin/status UI.

## 8. Repo bookkeeping (part of slice 2)

ROADMAP #6 slice-2 checkboxes; **ADR-0016** recording D9–D17 (supplements ADR-0015, does not supersede it); TASKS.md entry; README updates (new endpoints, replay, domain discriminator). (Note: the roadmap's *future-ADR* list 0016–0020 renumbers again to 0017–0021 — reserved numbers yield to shipped ADRs, same rule as last time.)
