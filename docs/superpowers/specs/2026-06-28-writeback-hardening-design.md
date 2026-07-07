# #6 Writeback Hardening — provenance history · rollback · shadow-mode — Design

**Date:** 2026-06-28
**Status:** Proposed
**Sub-project:** #6 eMRO Writeback REST API (local hardening slice, against `fake_emro`)
**Authoritative inputs:**
[#6 sub-plan](../../plans/2026-04-14-writeback-rest-plan.md) ·
[ADR-0003 fake_emro contract testing](../../adr/0003-fake-emro-contract-testing.md) ·
[ADR-0005 deterministic agent spine](../../adr/2026-06-27-0005-deterministic-agent-spine-core.md)

## 1. Context

Writeback is the **only** agent with eMRO write permission, so it is the highest-blast-radius boundary in the system. Today the seam is functional but thin: `WritebackTarget.write(req) -> WritebackResult`, an `InMemoryWritebackTarget` (idempotency-key dedup + open-order deferral + a success-only `.history` list), a `RestWritebackClient` (httpx), and a `fake_emro` FastAPI mock (`POST /inventory-levels`, minimal `GET /history`). It has **no provenance history, no rollback, and no shadow-mode** — the three things the #6 sub-plan and SOC 2 require before a real eMRO write is safe.

This slice hardens the seam **locally, against `fake_emro`**, mirroring the contract's behavior so the eventual real-eMRO Java implementation is de-risked. The real Oracle/Spring endpoint, auth, business-rule validation, rate limiting, and persistence stay deferred behind the existing seam (per ADR-0003: the wire/behavior contract is orthogonal to deployment).

## 2. Scope

**In scope (locally verifiable):**
1. **Provenance history** — a rich `HistoryEntry` recorded on every applied write (monotonic `version` per `(tenant, pn, location)`, `parent_version` chain, full provenance); a `get_history()` query; `fake_emro GET /history` serializing full entries.
2. **Rollback** — `rollback(RollbackRequest) -> RollbackResult` reverting the latest applied write to its prior values, within a configurable non-zero `rollback_window_days` (default 90); emits a new `parent_version`-linked entry under a **planner** principal; `fake_emro POST /rollback`.
3. **Shadow-mode** — a `shadow` flag on the write path + `WritebackStatus.SHADOWED`: logs a history entry and computes old→new but **does not mutate**; surfaced as `trax-io-spine run --shadow` end to end.
4. **Seam extension** — an `AuditedWritebackTarget` Protocol extending `WritebackTarget`; `InMemoryWritebackTarget`, `RestWritebackClient`, and `fake_emro` all implement it.

**Deferred (tracked in ROADMAP):** the real eMRO Oracle/Spring REST endpoint; mTLS + JWT + service/planner-principal auth; eMRO-side business-rule validation (MinOQ/shelf-life/hazmat — eMRO enforces even if the Guardrail approves); rate limiting (429); **bulk-rollback** + confirmation-token flow; idempotency body-mismatch 409; persistent history (S3/DynamoDB) + 30/90-day retention GC; Schemathesis; the `stock_level_changed` event emission; web-frontend rollback authorization.

**Non-goals:** changing the recommendation engine, the guardrail, or the autonomy decision; changing `write()`'s base signature; touching the four-column write scope.

## 3. Contracts (`services/agent-spine/src/trax_io_spine/contracts.py`)

All frozen pydantic (`ConfigDict(frozen=True, extra="forbid")`), matching the existing `WritebackRequest`/`WritebackResult` pattern.

- `WritebackStatus` gains `SHADOWED = "shadowed"` (alongside `WRITTEN`, `DEFERRED_OPEN_ORDER`, `FAILED`).
- `WritebackRequest` gains (both **backward-compatible defaults** so existing callers/tests are unaffected):
  - `tier: AutonomyTier | None = None` — provenance: the autonomy tier the supervisor applied.
  - `shadow: bool = False` — when true, compute + log but do not apply.
- `RollbackStatus(StrEnum)`: `ROLLED_BACK`, `OUTSIDE_WINDOW`, `NOTHING_TO_REVERT`.
- `HistoryEntry(_Base)`: `tenant_id, pn, location, version (int, ≥1 monotonic per key), status (WritebackStatus), old_values (dict[str,int] | None), new_values (dict[str,int]), provenance_id (str), tier (AutonomyTier | None), agent_version (str), changed_by_principal (str), idempotency_key (str | None), parent_version (int | None), changed_at (datetime)`.
- `RollbackRequest(_Base)`: `tenant_id, pn, location, reason (str), principal (str = "planner"), requested_at (datetime)`.
- `RollbackResult(_Base)`: `tenant_id, pn, location, status (RollbackStatus), from_values (dict[str,int] | None), to_values (dict[str,int] | None), reverted_from_version (int | None), new_version (int | None), rolled_back_at (datetime | None), error_message (str | None)`.

The four write columns remain `rop, eoq, safety_stock, max_stock`; `old_values`/`new_values` are dicts over exactly these keys.

## 4. Seam (`writeback/target.py`)

```python
class AuditedWritebackTarget(WritebackTarget, Protocol):
    def get_history(self, *, tenant_id: str, pn: str, location: str) -> tuple[HistoryEntry, ...]: ...
    def rollback(self, req: RollbackRequest) -> RollbackResult: ...
```

`WritebackTarget.write` is **unchanged** — the Supervisor depends only on it, so nothing downstream breaks. The audited methods are the hardening surface.

### 4.1 `InMemoryWritebackTarget` (the deterministic reference impl)

- Constructor gains `rollback_window_days: int = 90` — **validated `> 0`** at construction (zero/negative raises; the contract forbids a zero window).
- Keeps a per-key history ledger: `_history: dict[(tenant,pn,location), list[HistoryEntry]]` with monotonic `version` starting at 1.
- `write(req)`:
  - idempotency + open-order deferral unchanged.
  - **shadow** (`req.shadow`): append a `HistoryEntry(status=SHADOWED, version=next, old_values=current, new_values=intended, parent_version=prior_applied_version, tier, provenance_id, changed_by_principal="agent-spine", agent_version)`, **do not** mutate `_levels`; return `WritebackResult(status=SHADOWED, old_values=current, new_values=intended)`.
  - **applied** write: mutate `_levels`, append `HistoryEntry(status=WRITTEN, …)`, return as today (now also stamping the entry).
- `get_history(...)`: returns the per-key ledger as a tuple (chronological).
- `rollback(req)`:
  - find the latest `WRITTEN` entry for the key. None → `NOTHING_TO_REVERT`.
  - if its `changed_at` is older than `rollback_window_days` (relative to `req.requested_at`) → `OUTSIDE_WINDOW` (no mutation).
  - else revert `_levels` to that entry's `old_values`; append a new `HistoryEntry(status=WRITTEN, version=next, old_values=<current applied new_values>, new_values=<reverted-to old_values>, parent_version=<that entry's version>, changed_by_principal=req.principal, provenance_id="rollback:"+reason-ish)`; return `ROLLED_BACK` with `from_values`/`to_values`/`reverted_from_version`/`new_version`.

Shadow entries never participate in the applied-version chain that rollback walks (rollback only considers `WRITTEN`).

### 4.2 `RestWritebackClient` (implements `AuditedWritebackTarget`)

- `write` sends `shadow` (and `tier`, `provenance_id`) in the body; maps `200→WRITTEN`, `202/"shadowed"→SHADOWED`, `409→DEFERRED_OPEN_ORDER`, else `FAILED`.
- `get_history` → `GET /history` (parses `HistoryEntry` list).
- `rollback` → `POST /rollback` (parses `RollbackResult`).

### 4.3 `fake_emro` (mirrors the contract surface)

- `POST /inventory-levels` honors `shadow` (logs a SHADOWED history row, returns the shadowed result, no level change).
- `GET /history?...` returns full `HistoryEntry` JSON for a key.
- `POST /rollback` runs the same rollback semantics, returns `RollbackResult`.
- Backed by an in-process `InMemoryWritebackTarget` so fake and in-memory share one behavior definition (single source of truth — no mock drift).

## 5. Supervisor & CLI integration (minimal)

- `Supervisor.__init__` gains `shadow: bool = False`; `to_writeback_request(...)` sets `tier=<applied tier>` and `shadow=self._shadow`. The base orchestration is otherwise unchanged.
- `trax-io-spine run` gains `--shadow/--no-shadow` (default off): constructs the Supervisor in shadow mode; every approved write is logged as `SHADOWED`, nothing is applied — the onboarding/validation mode. (`ingest` can adopt it later.)

## 6. Testing strategy

- **contracts** — new types round-trip; `WritebackStatus.SHADOWED`/`RollbackStatus` present; `WritebackRequest` defaults keep old construction valid.
- **history** — a write records a `HistoryEntry` with correct `version` (1, then 2…), `old_values`/`new_values`, `parent_version` chain, tier/provenance; `get_history` returns them chronologically; idempotent re-write does not double-log.
- **shadow** — `write(shadow=True)` returns `SHADOWED`, logs a `SHADOWED` entry, and **leaves `_levels` unchanged** (a subsequent real read shows the old value); a later applied write still versions correctly.
- **rollback** — after an applied write, `rollback` reverts `_levels` to the prior values, returns `ROLLED_BACK` with correct from/to + a new linked entry; rollback with no prior write → `NOTHING_TO_REVERT`; an entry older than the window → `OUTSIDE_WINDOW` (no mutation); `rollback_window_days=0` raises at construction.
- **fake_emro / rest** (`--extra emro`) — `POST /inventory-levels` shadow vs applied; `GET /history` returns full entries; `POST /rollback` reverts; `RestWritebackClient.get_history`/`rollback`/shadow round-trip against the ASGI app.
- **supervisor / CLI** — a shadow-mode Supervisor run applies nothing but logs SHADOWED history; `trax-io-spine run --shadow` over the extract sample prints a summary with zero applied writes; the default (non-shadow) path is unchanged (existing 34 agent-spine writeback/integration tests still green).

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Touching `WritebackRequest`/Supervisor breaks existing tests | New request fields are defaulted; `write()` signature unchanged; supervisor change is two assignments. Existing suite re-run as a gate. |
| Shadow entries corrupt the rollback chain | Rollback walks only `WRITTEN` entries; shadow entries are audit-only. |
| Mock drift between `fake_emro` and `InMemoryWritebackTarget` | `fake_emro` is backed by an `InMemoryWritebackTarget` instance — one behavior definition, not two. |
| Over-building eMRO-only concerns | Auth, business rules, rate limiting, bulk-rollback, persistence, events all explicitly deferred; the legacy-Java grounding confirms these are not the fake's job. |

## 8. Deliverables

- `contracts.py` new types + `WritebackStatus.SHADOWED`; `AuditedWritebackTarget` Protocol; hardened `InMemoryWritebackTarget`, `RestWritebackClient`, `fake_emro`; supervisor `shadow` + `tier`; `trax-io-spine run --shadow`. Full tests (`--extra emro` for the FastAPI ones), ruff-clean.
- ADR-0010 (audited writeback seam; provenance/rollback/shadow against fake_emro; eMRO concerns deferred).
- CLAUDE.md `--shadow` note; ROADMAP #6 entry; TASKS.md.
