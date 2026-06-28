# ADR-0010: #6 writeback hardening — AuditedWritebackTarget seam (provenance · rollback · shadow)

**Date:** 2026-06-28
**Status:** Accepted
**Context project:** #6 eMRO Writeback REST API (local hardening slice, against `fake_emro`)

## Context

Writeback is the **only** agent with eMRO write permission — the highest-blast-radius boundary in the system. The seam existed (`WritebackTarget.write`, `InMemoryWritebackTarget`, `RestWritebackClient`, a thin `fake_emro`) but lacked the three things the #6 sub-plan and SOC 2 require before a real eMRO write is safe: **provenance history**, **rollback**, and **shadow-mode**. This slice adds them locally, against `fake_emro`, mirroring the contract so the eventual real-eMRO Java implementation is de-risked (per ADR-0003: wire/behavior contract is orthogonal to deployment).

## Decision

Extend the seam with an `AuditedWritebackTarget` Protocol and harden the in-memory reference, the fake, and the REST client.

- **`AuditedWritebackTarget(WritebackTarget, Protocol)`** adds `get_history(*, tenant_id, pn, location) -> tuple[HistoryEntry, ...]` and `rollback(req) -> RollbackResult`. The base `write()` is unchanged, so the Supervisor's dependency on it — and every existing caller — is untouched. New `WritebackRequest` fields (`tier`, `shadow`) are defaulted for backward compatibility.
- **Provenance history.** `InMemoryWritebackTarget` keeps a per-key `HistoryEntry` ledger recorded on every applied write: monotonic `version` (from 1), `parent_version` chaining the prior `WRITTEN` entry, and full provenance (`old/new_values`, `provenance_id`, `tier`, `agent_version`, `changed_by_principal`, `idempotency_key`, `changed_at`). The legacy `.history` (success-only `WritebackResult` list) is preserved.
- **Rollback.** `rollback` reverts the latest `WRITTEN` entry to its `old_values` within a configurable, **non-zero** window (`rollback_window_days=90`, validated `> 0`), emits a new `parent_version`-linked `WRITTEN` entry under the request's principal (default `planner`), and returns `ROLLED_BACK` / `OUTSIDE_WINDOW` / `NOTHING_TO_REVERT`. Reverting the first-ever write (no captured prior state) is `NOTHING_TO_REVERT`. Rollback walks **only** `WRITTEN` entries, so shadow rows never corrupt the chain.
- **Shadow-mode.** A `shadow` flag + `WritebackStatus.SHADOWED`: a shadow write logs a `SHADOWED` `HistoryEntry` and computes old→new but **does not mutate** `_levels` or `.history`. Surfaced as `trax-io-spine run --shadow`.
- **No mock drift.** `fake_emro` is **backed by a single `InMemoryWritebackTarget`** instance — the FastAPI mock and the in-memory reference share one behavior definition, exactly the "mock drift rejected at the high-blast-radius boundary" the contract demands. `RestWritebackClient` mirrors the surface (write/shadow/history/rollback) over httpx.

### Shadow mode logs would-be-queued writes too (divergence from the written spec)

The spec scoped shadow logging to the `APPROVED_FOR_WRITE` branch only. The implementation also intercepts the **`QUEUED_FOR_APPROVAL`** branch: in shadow mode, any recommendation carrying a **writable policy** is shadow-logged (and *not* queued), whether it would normally be auto-written or queued for approval; recommendations without a writable policy stay queued; nothing is applied. This is **more design-faithful** — the design's shadow/onboarding mode exists to log *every* intended write for the Evaluation Pipeline to score against actual planner decisions, not just the subset that would auto-write. Observed on the extract sample: `run --shadow` → `{written: 0, shadowed: 2, queued: 2, rejected: 2}` (the 2 shadowed are the recs with concrete policies; the 2 still-queued are advisory). `SHADOWED` results are routed into a new `OrchestrationResult.shadowed` bucket + `summary["shadowed"]`.

## Consequences

**Positive**
- Full provenance, rollback, and a real onboarding shadow mode — all locally verifiable against `fake_emro`, no AWS (88 agent-spine tests; live `--shadow` verified).
- The base `write()` contract and the Supervisor are unchanged; the hardening is additive behind the extended Protocol.
- One behavior definition (`fake_emro` backed by `InMemoryWritebackTarget`) eliminates the mock-drift risk the contract calls out.

**Negative / deferred (real eMRO / out of local scope)**
- The real Oracle/Spring REST endpoint; mTLS + JWT + service/planner-principal auth; eMRO-side business-rule validation (MinOQ/shelf-life/hazmat — eMRO enforces even if the Guardrail approves); rate limiting (429); **bulk-rollback** + confirmation-token flow; idempotency body-mismatch 409; persistent history (S3/DynamoDB) + retention GC; Schemathesis; the `stock_level_changed` event emission.
- `rollback` reverts a single key's latest write; multi-step undo is a chain of single rollbacks.

## Alternatives considered

1. **Add optional methods directly to `WritebackTarget`.** Rejected: a Protocol with optional methods is awkward and weakens the base contract. A separate extending Protocol keeps `write()` clean and the hardening explicit.
2. **A parallel reimplementation of history/rollback/shadow inside `fake_emro`.** Rejected outright: two behavior definitions drift, and drift at the writeback boundary is unacceptable. Backing the fake with `InMemoryWritebackTarget` is the single source of truth.
3. **Shadow as a wrapper target.** Rejected: a wrapper can't cleanly read the inner target's current levels; a `shadow` flag on the request is wire-expressible and works identically for in-memory and REST.
