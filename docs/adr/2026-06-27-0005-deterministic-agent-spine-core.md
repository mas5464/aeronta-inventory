# ADR-0005: Agent Spine v1 is a deterministic orchestration core behind Protocols

**Date:** 2026-06-27
**Status:** Accepted
**Context project:** #4 Agent Spine

## Context

The 2026-04-14 Agent Spine plan ([docs/plans/2026-04-14-agent-spine-implementation-plan.md](../plans/2026-04-14-agent-spine-implementation-plan.md)) specified an LLM-agent topology — a Strands Supervisor on Bedrock AgentCore Runtime dispatching to specialist subagents — and **stubbed** the Feature Store (#2), Forecasting/Policy (#5), and eMRO Writeback (#6). By the time #4 was built, #2 and #11 were real and deterministic:

- **#2 Feature Store** ships `FeatureStoreClient` + `InMemoryFeatureStore`/`GlueIcebergFeatureStore`/`DynamoDbOnlineStore` + `FeatureBundle`.
- **#11 Recommendation Engine** is a deterministic, non-LLM engine: `RecommendationService.run(tenant, keys, now) → RecommendationBatch`. Each `Recommendation` already carries `suggested_autonomy_tier`, `guardrail_flags`, the §6.2-clamped `policy` (ROP/EOQ/SS/Max), `current_policy`, `criticality_tier`, and a content-addressed `input_snapshot_hash`.

So #11 *suggests* an autonomy tier and *flags* guardrails but does not *enforce* them, route approvals, or write back. The design (§3) also mandates that the Policy Engine and Writeback are non-LLM.

## Decision

Build Agent Spine v1 as a **deterministic Python orchestration core** in the monorepo (`services/agent-spine/`, `trax_io_spine`) that sits **downstream of `RecommendationService.run()`** and does exactly the three things #11 does not: **enforce** the effective autonomy tier, **route** approvals, and **write back**. Every collaborator is a Protocol seam so the design-§3 LLM/AgentCore topology, Cedar, and the real #6 endpoint swap in later without re-architecture:

- `AutonomyPolicy` — deterministic `BandAutonomyPolicy` now (tenant band rules: tier × delta% × criticality floor); **Cedar backs the same Protocol** in the deployment slice.
- `WritebackTarget` — `InMemoryWritebackTarget` (tests/dry-run) and `RestWritebackClient` (httpx) against a `fake_emro` FastAPI harness; the **real #6 endpoint** implements the same contract.
- `FeatureStoreClient` — the real #2 client (in-memory for the offline dry run), via #11's `ContextAssembler` (reused, not duplicated).

The hard §6.2 guardrails are **verified** (defense-in-depth over #11's clamps), not re-clamped: a breach is emitted as `rejected_hard_guardrail` and logged, never silently written. The writeback idempotency key is the recommendation's **content-addressed `input_snapshot_hash`** (re-running the same extract dedups; a new snapshot is a new write) rather than run date.

This mirrors [ADR-0004](2026-04-17-0004-deterministic-recommendation-layer.md) (deterministic recommendation layer) and #2's own "Protocol-first, in-memory-then-real" pattern (ADR-0002).

## Consequences

**Positive**
- Ships an end-to-end tiered-autonomy run with **no AWS/LLM/Cedar** — `trax-io-spine run --extract-dir … --tenant …` delivered roadmap milestone #8 (6 recommendations → 4 queued, 2 hard-guardrail-rejected, 0 mis-applied) deterministically and offline.
- Reuses the real #2/#11 rather than stubbing them; re-exports #11's contract mirrors instead of redefining them, so there is no drift.
- The LLM Supervisor, Cedar, AgentCore Runtime/Memory, the event lane, and the real #6 endpoint are pure additions behind existing seams.

**Negative / deferred**
- No LLM reasoning or explanations in v1 (the design's Sonnet/Haiku roles are deferred).
- Cedar policy bands are approximated by a deterministic `BandAutonomyPolicy` until the deployment slice.
- `RestWritebackClient` wraps `httpx.AsyncClient` + `asyncio.run()` (httpx 0.28 made `ASGITransport` async-only); correct for the sync-only v1, to be revisited if the spine becomes async.

## Alternatives considered

1. **Full Strands/AgentCore LLM topology now (faithful to the 2026-04-14 plan), separate repo.** Rejected: heavy, AWS-coupled, slow to a working batch, and would re-stub the now-real #2/#11.
2. **Net-new layers only (guardrail + writeback), no Supervisor.** Rejected: no end-to-end run, so milestone #8 unreachable.
