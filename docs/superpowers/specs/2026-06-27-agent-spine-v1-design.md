# Trax IO — Agent Spine v1 (Deterministic Orchestration Core) — Design

**Date:** 2026-06-27
**Sub-project:** #4 Agent Spine
**Status:** Design — approved in brainstorm, pending spec review → writing-plans
**Supersedes (in part):** [docs/plans/2026-04-14-agent-spine-implementation-plan.md](../../plans/2026-04-14-agent-spine-implementation-plan.md) — that plan predates the real #2/#11 builds and assumes a separate repo + stubs. This design adapts it to today's reality.

---

## 1. Context & why this shape

The 2026-04-14 Agent Spine plan was written before sub-projects #2 and #11 existed, so it assumed a standalone `trax-io-agent-spine` repo and **stubbed** the feature store, forecasting, and policy engine. Those are now real and live in this monorepo:

- **#2 Feature Store** (`services/feature-store/`): `FeatureStoreClient` Protocol + `InMemoryFeatureStore` + `GlueIcebergFeatureStore` (offline) + `DynamoDbOnlineStore` (online) + `FeatureBundle`. `TenantContext` is defined here and is the canonical tenant binding the read path requires.
- **#11 Recommendation Engine** (`services/recommendation-engine/`): deterministic. `RecommendationService(feature_store, inventory_state).run(tenant, keys, now) → RecommendationBatch`. Each `Recommendation` already carries `suggested_autonomy_tier`, `guardrail_flags`, the §6.2-clamped `policy: PolicyRecommendation` (ROP/EOQ/SS/Max), `current_policy`, `criticality_tier`, and `input_snapshot_hash`. The engine ships forward-compatible contract mirrors (`AutonomyTier`, `PolicyRecommendation`, `Regime`, `CanonicalCriticality`, `PolicyKind`) explicitly "for promotion to Agent Spine #4."

So #11 **suggests** an autonomy tier and **flags** guardrails but does not **enforce** them, route approvals, or write to eMRO. The Agent Spine's net-new job is therefore the **orchestration + autonomy enforcement + writeback** layer that sits *downstream of `RecommendationService.run()`*.

### Approach (chosen in brainstorm)

A **deterministic Python orchestration core** in the monorepo, behind Protocols so the design-§3 LLM topology (Strands + Bedrock AgentCore Runtime, Claude Sonnet/Haiku) slots in later with **no re-architecture** — the same "Protocol-first, deterministic-v1" pattern #2 used (in-memory stub → real backend behind one Protocol). This matches ADR-0004 (deterministic recommendation layer) and the design's own rule that the Policy Engine and Writeback are non-LLM.

Two brainstorm decisions:
- **Autonomy bands:** a deterministic, testable `AutonomyPolicy` (tenant-config band rules) behind a Protocol; **Cedar backs the same Protocol in production** (deployment slice). No AWS/Cedar dependency in this slice.
- **Context assembly:** **reuse #11's assembler** (`ContextAssembler`/`FeatureReader` over the `FeatureStoreClient`) — single source of truth, no duplicated assembly.

---

## 2. Scope

### In scope (this slice)
1. New monorepo package `services/agent-spine/` (`trax_io_spine`), `uv` + `pytest` + `ruff`, depending on `trax-io-feature-store` and `trax-io-reco` via non-editable path sources (the established convention — see `.claude/memory/lessons.md`).
2. **Identity:** `tenant_scope`/`current_tenant` contextvar propagation around the feature-store `TenantContext` (the multi-tenant chokepoint at the orchestration layer).
3. **Guardrail & Autonomy enforcement:** turn each #11 `Recommendation` into a `GuardrailOutcome` — verify §6.2 hard invariants (defense-in-depth), then authorize against the tenant's autonomy bands → `approved_for_write | queued_for_approval | rejected_hard_guardrail | deferred`.
4. **Writeback:** async idempotent `WritebackClient` → eMRO Writeback REST, tested against an in-repo `fake_emro` FastAPI harness (since #6 is not built). Approved outcomes only; open-order deferral handled.
5. **Supervisor:** the deterministic orchestrator — bind tenant → build context from #2 → `RecommendationService.run()` → per-rec enforce → write approved / queue the rest → `OrchestrationResult`.
6. **Contracts:** promote #11's mirrors; add `GuardrailOutcome`, `ApprovalTask`, `WritebackRequest`/`WritebackResult`, `OrchestrationResult`.
7. **CLI:** `trax-io-spine run --extract-dir … --tenant …` — an offline end-to-end dry run delivering roadmap milestone #8 ("first Agent Spine recommendation produced") with no AWS.

### Out of scope (designed-for, built later)
- Strands / Bedrock AgentCore Runtime deployment; LLM Supervisor & specialist agents; LLM explanations.
- Cedar (`cedarpy`) — deferred behind the `AutonomyPolicy` Protocol.
- AgentCore Memory; the event lane (DynamoDB/online-triggered recompute); AgentCore Gateway/Identity/Observability; CDK stacks.
- The real #6 eMRO Writeback endpoint (we ship the contract + a fake).

---

## 3. Architecture

```
                         services/agent-spine/  (trax_io_spine)
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Supervisor.run(tenant, keys, now)                                     │
  │     │                                                                  │
  │     │ 1. tenant_scope(tenant)         [identity/]                      │
  │     ▼                                                                  │
  │  RecommendationService(feature_store, inventory_state)  ── reuse #11 ──┼──► #2 FeatureStoreClient
  │     .run(tenant, keys, now) ─► RecommendationBatch                     │     (InMemory / Iceberg / online)
  │     │                                                                  │
  │     ▼ 2. for each Recommendation                                       │
  │  GuardrailEnforcer.enforce(rec, policy)  ─► GuardrailOutcome  [guardrail/]
  │     │      • verify §6.2 hard invariants (defense-in-depth)            │
  │     │      • AutonomyPolicy.authorize(tier, delta%, criticality)       │
  │     ▼ 3. route                                                         │
  │  approved ─► WritebackClient.write(req)  [writeback/] ──HTTP idempotent┼──► fake_emro (FastAPI)  →  #6 REST
  │  queued   ─► ApprovalTask                                              │
  │  rejected / deferred ─► recorded                                       │
  │     │                                                                  │
  │     ▼ 4.                                                               │
  │  OrchestrationResult { written, queued, rejected, deferred, skipped }  │
  └──────────────────────────────────────────────────────────────────────┘
```

Every box is a unit with one purpose, a typed interface, and explicit dependencies — independently testable. The Supervisor depends only on Protocols (`FeatureStoreClient`, `AutonomyPolicy`, `WritebackTarget`), so the LLM/Cedar/real-eMRO implementations swap in by DI.

---

## 4. Components

### 4.1 `identity/` — tenant chokepoint
- **Purpose:** bind a `TenantContext` task-locally so every orchestration step runs under one tenant; reading tenant outside a scope raises.
- **Interface:** `tenant_scope(ctx: TenantContext)` (context manager) and `current_tenant() -> TenantContext` (raises `MissingTenantContextError` outside a scope), over `contextvars` (async-safe).
- **Depends on:** feature-store `TenantContext` (imported, not redefined).

### 4.2 `contracts/` — typed seams
- **Purpose:** the spine's own contracts plus re-exports of #11's mirrors.
- **Re-export from #11:** `AutonomyTier`, `PolicyRecommendation` (+ `Regime`, `CanonicalCriticality`, `PolicyKind` as needed).
- **New (pydantic v2, `frozen=True, extra="forbid"`):**
  - `GuardrailStatus` (StrEnum): `approved_for_write | queued_for_approval | rejected_hard_guardrail | deferred`.
  - `GuardrailOutcome { status, tier: AutonomyTier, recommendation_id, delta_pct, reasons: tuple[str,...], approval_task: ApprovalTask | None }`.
  - `ApprovalTask { task_id, tenant_id, pn, location, priority_score, tier, reason }`.
  - `WritebackRequest { tenant_id, pn, location, rop, eoq, safety_stock, max_stock, provenance_id, idempotency_key }`.
  - `WritebackResult { tenant_id, pn, location, status: written|deferred_open_order|failed, old_values?, new_values?, written_at?, error_message? }`.
  - `OrchestrationResult { tenant_id, generated_at, written: tuple[WritebackResult,...], queued: tuple[ApprovalTask,...], rejected: tuple[GuardrailOutcome,...], deferred: tuple[WritebackResult,...], skipped: tuple[SkippedKey,...], summary }`.

### 4.3 `guardrail/` — enforcement (the core net-new logic)
- **Purpose:** convert #11's *suggestion* into an *enforced decision*.
- **`hard_guardrails.py`** — non-bypassable §6.2 verifiers run first, as defense-in-depth over #11's clamps. They re-derive `delta_pct` from `policy` vs `current_policy` and assert: single-write delta ≤ 100% (even Tier C); shelf-life / hazmat / tool-control clamps respected; **active AOG signal forces Tier A**. A violation → `rejected_hard_guardrail` (the engine should already have clamped, so a violation is a contract breach worth catching, not silently passing).
- **`policy.py`** — `AutonomyPolicy` Protocol + `BandAutonomyPolicy` (deterministic). Given `(suggested_tier, delta_pct, criticality_tier, guardrail_flags)` and a tenant's `AutonomyConfig` (per-tier delta-band ceilings + criticality floor for autonomous writes), returns the **effective** status: within the autonomous band → `approved_for_write`; outside → `queued_for_approval` (with a `priority_score`). Cedar backs this Protocol in production.
- **`enforce.py`** — `GuardrailEnforcer.enforce(rec) -> GuardrailOutcome` composing the two: hard verify → band authorize. Pure, fully unit-testable, no I/O.
- **Depends on:** contracts only.

### 4.4 `writeback/` — the only write surface
- **Purpose:** persist approved `(ROP, EOQ, SS, Max)` to eMRO, idempotently.
- **`client.py`** — `WritebackTarget` Protocol + `RestWritebackClient` (httpx async): `write(req: WritebackRequest) -> WritebackResult`. Idempotency key `f"{extract_date}:{tenant}:{pn}:{location}"`; a duplicate key is a no-op returning the prior result. Maps a `PolicyRecommendation` → `WritebackRequest`. Open-order conflict from the endpoint → `deferred_open_order` (not `failed`).
- **`fake_emro/server.py`** — FastAPI mock implementing the same OpenAPI surface (in-memory `PN_INVENTORY_LEVEL` + history), honoring idempotency + an injectable open-order conflict, so integration tests run with no AWS and the contract is pinned for #6.
- **Depends on:** contracts; httpx.

### 4.5 `supervisor/` — deterministic orchestrator
- **Purpose:** the end-to-end sequence; the seam the LLM Supervisor later wraps.
- **Interface:** `Supervisor(feature_store, inventory_state, autonomy, writeback, config).run(tenant, keys, now) -> OrchestrationResult`.
- **Flow:** `tenant_scope(tenant)` → `RecommendationService(...).run(tenant, keys, now)` → for each `Recommendation`: `enforce()` → route (`approved` → `writeback.write()`; `queued` → `ApprovalTask`; `rejected`/`deferred` → record) → assemble `OrchestrationResult` (carrying the batch's `skipped` through).
- **Depends on:** `RecommendationService` (#11), `GuardrailEnforcer`, `WritebackTarget`, identity. All injected.

### 4.6 `cli/` — offline end-to-end
- `trax-io-spine run --extract-dir <dir> --tenant <id> [--apply/--dry-run]`: `build_stores_from_extract` (#11 bridge) → `Supervisor.run()` → print `OrchestrationResult`. `--dry-run` (default) uses an in-memory fake writeback target; `--apply` points at a running `fake_emro` (or, later, the real endpoint via `--writeback-url`).

---

## 5. Data flow (worked example)

`extract dir → build_stores_from_extract → (InMemoryFeatureStore, InventoryStateProvider, tenant_id, keys)`
→ `Supervisor.run(TenantContext(tenant_id), keys, now)`
→ `RecommendationService.run()` yields a `RecommendationBatch` (e.g. 6 recs, 0 skipped)
→ per rec: `enforce()` →
  - a Tier-C (`autonomous`) ROP bump of +18% on a Tier-4 part within band → `approved_for_write` → `writeback.write()` → `written`
  - a Tier-B part with +60% delta over the tenant's bounded ceiling → `queued_for_approval` (`ApprovalTask`)
  - a Tier-1 (AOG/flight-safety) part → forced Tier A → `queued_for_approval`
  - a part with an open replenishment order covering the gap → `deferred_open_order`
→ `OrchestrationResult { written: [...], queued: [...], deferred: [...], rejected: [], skipped: [] }`.

---

## 6. Testing strategy

- **Unit** (no I/O): hard-guardrail verifiers (delta cap, AOG→Tier A, shelf-life/hazmat/tool); `BandAutonomyPolicy` band edges (just-inside / just-outside ceilings, criticality floor); writeback idempotency + open-order deferral mapping; identity scope/clear + async task-locality; contract round-trips.
- **Integration**: end-to-end `Supervisor.run()` over a committed seed/extract → `fake_emro`, asserting the routing of the §4-scenario mix (approved write lands in `fake_emro` history; queued/deferred/rejected counts), and **tenant isolation** (a cross-tenant key cannot be assembled/written).
- **Conventions:** mirror #2/#11 — `uv run --extra dev pytest`, `ruff` (line-length 100, select E/F/I/B/UP/N/SIM), `pythonpath=["src"]`, src-layout, pydantic `frozen=True`. fake_emro behind an `api`/`emro` extra so core tests need no FastAPI.
- **Adversarial review** of the guardrail enforcement + writeback idempotency after build (the established cadence).

---

## 7. Milestones (this slice)

1. Package scaffold + contracts (promote #11 mirrors) green.
2. Identity + guardrail enforcement (hard + band) green, unit-tested.
3. Writeback client + fake_emro harness green.
4. Supervisor wiring + CLI → **end-to-end offline run produces an `OrchestrationResult`** (roadmap milestone #8).
5. Integration tests (routing + tenant isolation) + adversarial review.

---

## 8. Open decisions — resolved

| Decision | Resolution |
|---|---|
| Repo placement | Monorepo `services/agent-spine/` (consistent with #1/#2/#9/#11). |
| LLM vs deterministic v1 | Deterministic core behind Protocols; LLM/AgentCore deferred, designed-for. |
| Autonomy bands | Deterministic `BandAutonomyPolicy` now; Cedar behind the same Protocol later. |
| Context assembly | Reuse #11's `ContextAssembler`/`FeatureReader`; no duplication. |
| Writeback target | `fake_emro` FastAPI harness now; real #6 endpoint via the same `WritebackTarget` Protocol. |
| Tenant binding | Reuse feature-store `TenantContext`; add contextvar propagation. |
| Tier ownership | #11 *suggests* `suggested_autonomy_tier`; the Spine *enforces* the effective tier + outcome. |

---

## 9. Risks

- **Contract drift between #11's mirrors and the Spine.** Mitigation: re-export #11's enums/models rather than redefine; a contract test asserts the Spine consumes #11's `Recommendation` shape verbatim.
- **fake_emro diverging from the real #6 OpenAPI.** Mitigation: pin the request/response schema in the contracts layer; #6 implements the same schema (ADR-style contract test, mirroring #2's in-memory↔Iceberg equivalence test).
- **Hard-guardrail double-implementation (#11 clamps, Spine verifies).** Intentional defense-in-depth; on a §6.2 invariant breach the Spine emits `rejected_hard_guardrail` and logs a contract-breach warning (it does **not** re-clamp and does **not** crash the run) — so the two layers cannot silently diverge, and a breach is surfaced rather than written.
