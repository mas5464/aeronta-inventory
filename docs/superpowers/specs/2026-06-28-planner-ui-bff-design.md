# #7 Planner UI — Backend-for-Frontend (BFF) — Design

**Date:** 2026-06-28
**Status:** Proposed
**Sub-project:** #7 Planner UI "Trax IO Review" (BFF slice; the React frontend follows as the next slice)
**Authoritative inputs:**
[#7 sub-plan](../../plans/2026-04-14-planner-ui-plan.md) ·
[ADR-0005 agent spine](../../adr/2026-06-27-0005-deterministic-agent-spine-core.md) ·
[ADR-0010 audited writeback](../../adr/2026-06-28-0010-audited-writeback-seam.md)

## 1. Context

The web frontend (`apps/web`, "Trax IO Review") is the sole customer-facing surface for reviewing, approving, and managing the agent's recommendations. The design splits it into a **React frontend embedded in eMRO** (next slice) and a **Trax-owned Python FastAPI backend-for-frontend (BFF)** — this slice.

The BFF is the **contract-defining, locally-verifiable** piece. Today the reco-API is read-only and the Agent Spine is CLI-only: the queue of `ApprovalTask`s the Supervisor produces is ephemeral (printed as JSON, then lost). The BFF gives that queue a home and a lifecycle — exposing the **pending-recommendations queue**, **provenance** drill-down, **approve / reject / defer / bulk-approve**, **writeback history + rollback**, and the per-tenant **kill switch** — over an **in-memory** store driven by the existing Supervisor / `GuardrailEnforcer` / `AuditedWritebackTarget`. No AWS, no eMRO, no auth handshake.

It lives in `services/agent-spine/src/trax_io_spine/bff/` behind a `[bff]` extra — the spine already owns the Supervisor, the contracts, the `GuardrailEnforcer`, and `InMemoryWritebackTarget`, so the BFF reuses them rather than reimplementing.

## 2. Scope

**In scope (locally verifiable):**
1. **Queue** — list pending recommendations for a tenant, priority-sorted (by the `ApprovalTask.priority_score` the guardrail already computes), filterable by status.
2. **Provenance detail** — the full `Recommendation` for one id (policy, demand projection, evidence, guardrail reasons).
3. **Actions** — approve (→ writeback apply + history), reject (with categorical reason), defer (with `deferred_until`).
4. **Bulk-approve** — by filter (tier / criticality / delta / type).
5. **History + rollback** — writeback audit trail per `(pn, location)`; trigger a rollback.
6. **Kill switch** — per-tenant toggle; while engaged, approve/bulk-approve are blocked (`423 Locked`) — the agent is paused.
7. **A `PlannerStore`** seeded from the extract sample (via the real Supervisor pipeline) + a `create_planner_app(store)` FastAPI factory.

**Deferred (tracked in ROADMAP):** the **React frontend** (next slice); real eMRO embedding; JWT + Cedar auth (stubbed — `tenant_id` is a trusted path param in v1); DynamoDB persistence; SSE real-time updates; the weekly **Tier-C digest** + **settings** (autonomy-bands / service-level) config surfaces; **bulk-rollback** + confirmation token; the NL-explanation agent (Claude Sonnet); the monthly BVR PDF; the federated override-feedback loop.

**Non-goals:** changing the recommendation engine, the guardrail, the autonomy decision, or the writeback seam; persistence; pagination beyond a simple `limit`.

## 3. Architecture

New module `services/agent-spine/src/trax_io_spine/bff/` (behind a `[bff]` optional extra = `fastapi`):

```
bff/
  __init__.py
  models.py   # BFF wire models: QueueRow, RecommendationDetail, RejectRequest, DeferRequest,
              #   BulkApproveFilter, ActionResult, KillSwitchState, TaskStatus(StrEnum)
  store.py    # PlannerStore: lifecycle state machine over (Recommendation, GuardrailOutcome) pairs
              #   + an InMemoryWritebackTarget; PlannerStore.from_extract(tenant, extract_dir, now)
  app.py      # create_planner_app(store) -> FastAPI; the endpoints
```

### 3.1 `PlannerStore`

Built once per tenant. `from_extract(tenant_id, extract_dir, *, now)`:
- `build_stores_from_extract` → `RecommendationService.run` → `batch.recommendations` (kept for provenance, keyed by `recommendation_id`).
- For each rec: `GuardrailEnforcer().enforce(rec)` → `GuardrailOutcome`.
  - `QUEUED_FOR_APPROVAL` → a `PENDING` queue entry (carries the rec id, the `ApprovalTask`, the outcome reasons).
  - `APPROVED_FOR_WRITE` → auto-applied immediately via the writeback target (appears in history; not in the pending queue).
  - `REJECTED_HARD_GUARDRAIL` → recorded as `rejected_by_guardrail` (not in the pending queue).
- Holds an `InMemoryWritebackTarget` (history + rollback), a `kill_switch: bool`, and the per-entry lifecycle `TaskStatus` (`PENDING / APPROVED / REJECTED / DEFERRED`).

Store methods (pure, deterministic, no I/O): `queue(*, status, limit)`, `detail(rec_id)`, `approve(rec_id)`, `reject(rec_id, reason, detail)`, `defer(rec_id, until)`, `bulk_approve(filter)`, `history(pn, location)`, `rollback(pn, location, reason)`, `set_kill_switch(engaged)`. Cross-tenant ids are unknown → 404. `approve`/`bulk_approve` while `kill_switch` is engaged raise a `KillSwitchEngaged` (→ `423`).

`approve(rec_id)` builds a `WritebackRequest` from the rec's `policy` (reusing the spine's `to_writeback_request`), writes it, marks the entry `APPROVED`, and returns the `WritebackResult`. A rec without a writable `policy` cannot be approved (`409`).

### 3.2 Endpoints (`create_planner_app`)

All under `/v1/tenants/{tenant_id}`; the app holds one `PlannerStore` per tenant (a `dict[str, PlannerStore]`, seeded lazily or injected for tests). `tenant_id` mismatch / unknown tenant → `404`.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/recommendations?status=pending&sort=priority&limit=50` | — | `QueueRow[]` (priority-desc) |
| GET | `/recommendations/{rec_id}` | — | `RecommendationDetail` (full provenance) |
| POST | `/recommendations/{rec_id}/approve` | — | `ActionResult` (writeback outcome) |
| POST | `/recommendations/{rec_id}/reject` | `RejectRequest{reason, detail?}` | `ActionResult` |
| POST | `/recommendations/{rec_id}/defer` | `DeferRequest{until?}` | `ActionResult` |
| POST | `/recommendations/bulk-approve` | `BulkApproveFilter` | `ActionResult{approved_count, results}` |
| GET | `/history?pn=&location=` | — | `HistoryEntry[]` |
| POST | `/rollback` | `RollbackRequest` | `RollbackResult` |
| GET | `/killswitch` | — | `KillSwitchState{engaged}` |
| POST | `/killswitch` | `KillSwitchState{engaged}` | `KillSwitchState` |

Status codes: `200` ok; `404` unknown tenant/rec; `409` approve a rec with no writable policy; `422` bad reason/body (pydantic); `423` action while kill switch engaged.

### 3.3 BFF models (`models.py`)

Frozen pydantic, mirroring the engine/spine contracts so the future TS types map 1:1:

- `TaskStatus(StrEnum)`: `PENDING, APPROVED, REJECTED, DEFERRED`.
- `RejectReason(StrEnum)`: `wrong_for_fleet, wrong_essentiality, bad_lead_time, planner_override, other`.
- `QueueRow`: `recommendation_id, pn, location, type, criticality_tier, aog_risk_level, confidence_score, recommended_quantity, estimated_cost_impact, suggested_autonomy_tier (tier), priority_score, status, reason`.
- `RecommendationDetail`: the queue-row fields **plus** `current_policy`, `policy` (proposed `rop/eoq/safety_stock/max_stock/policy_kind/service_level_target/model_id`), `demand_projection` (`mean_per_day/std_per_day/dist_kind/dist_params/historical_component/scheduled_component/basis_window_days`), `supporting_evidence[]`, `guardrail_flags`, `provenance_id`, `reason`.
- `RejectRequest{reason: RejectReason, detail: str = ""}`, `DeferRequest{until: datetime | None = None}`.
- `BulkApproveFilter{tiers?, max_delta_pct?, criticality_min?, types?}` (all optional; matches PENDING entries).
- `ActionResult{recommendation_id, status, writeback: WritebackResult | None, message}`.
- `KillSwitchState{engaged: bool}`.

`HistoryEntry`, `RollbackRequest`, `RollbackResult`, `WritebackResult` are reused verbatim from `contracts.py`.

## 4. Testing strategy

- **store** (no HTTP) — `from_extract` over the sample seeds a PENDING queue; `approve` writes + logs history + flips status; `reject` records the categorical reason; `defer` sets `deferred_until`; `bulk_approve` approves only matching PENDING; `approve` while kill-switch engaged raises; approving a no-policy rec raises; cross-tenant rec id → not found.
- **app** (FastAPI `TestClient`) — `GET /recommendations` returns priority-desc rows for the tenant; `GET /{id}` returns full provenance; `approve` → `200` + writeback, then `GET /history` shows the entry; `reject`/`defer` → status transitions; `bulk-approve` count; `POST /killswitch{engaged:true}` then `approve` → `423`; `POST /rollback` reverts; unknown tenant → `404`; unknown rec → `404`; approve no-policy → `409`.
- **tenant isolation** — a second tenant's store is independent; tenant A cannot see/act on tenant B's recommendations.

## 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Sample may have few/zero `QUEUED_FOR_APPROVAL` recs to exercise the queue | The extract sample yields ~4 queued (verified earlier: `run` → 4 queued / 2 rejected). Edge-case tests seed a controlled `PlannerStore` directly with `(Recommendation, GuardrailOutcome)` pairs. |
| Re-running the guardrail in the BFF duplicates Supervisor logic | The BFF deliberately keeps the `(rec, outcome)` pairs the Supervisor discards; it reuses `RecommendationService` + `GuardrailEnforcer` + `to_writeback_request` (no reimplementation). |
| Kill-switch semantics ambiguous | v1: engaged ⇒ approve/bulk-approve `423`; reads still work. Documented; the "revert to shadow" propagation is the real-runtime concern (deferred). |
| Auth absent | `tenant_id` is a trusted path param in v1 (stubbed); JWT + Cedar is an explicit deferral, behind the same endpoint shape. |

## 6. Deliverables

- `bff/` module (`models.py`, `store.py`, `app.py`) + the `[bff]` extra; full pytest suite (`--extra bff`), ruff-clean.
- A `trax-io-spine serve-planner --extract-dir … --tenant …` CLI (optional) or a documented `uvicorn` entry to run the BFF locally for the next (frontend) slice.
- ADR-0011 (Planner BFF in agent-spine; in-memory store reusing the Supervisor pipeline; React frontend + auth + persistence deferred).
- CLAUDE.md `[bff]` row/note; ROADMAP #7 entry; TASKS.md. The OpenAPI the BFF emits becomes the contract the React slice consumes.
