# ADR-0004 — Deterministic Recommendation Layer in v1

**Date:** 2026-04-17 · **Status:** Accepted · **Owner:** Miguel Sosa, VP Head of Innovation

## Context

The locked design §8 phases the inventory-action capabilities: AOG risk → v3, Transfer/Reduce/Sell → v4, Purchase/sourcing → v5; v1 (decision Q1) is "dynamic stock-level tuning" — recomputing `(ROP, EOQ, Safety Stock, Max)` and writing back to `PN_INVENTORY_LEVEL`. The owner has chosen to ship a **deterministic recommendation layer in v1** that produces all five recommendation types (Purchase, Transfer, Reduce Stock, Sell, Adjust Min/Max) as rule-based precursors over a shared net-position primitive, deferring the ML forecasting ensemble (#5), the Bedrock/Strands runtime (#4), eMRO writeback (#6), and the Planner UI (#7).

All eight required acceptance scenarios are deterministic outcomes over the net position; none requires the ML stack. The feature-store contract (#2) does not yet model on-hand stock or current policy, so those (plus AOG history, repair TAT, and forward scheduled demand) are served by an engine-owned `InventoryStateProvider` stub with documented promotion paths.

## Decision

Add a new sub-project — **Recommendation Engine (deterministic v1)** — at `services/recommendation-engine/` (`trax_io_reco`), seated in Wave 1. It depends on #2 (Feature Store read contract) and is forward-compatible with the #4/#5 contracts (`Regime`, `CanonicalCriticality`, `PolicyKind`, `PolicyRecommendation`, `AutonomyTier`, `ForecastHorizon`) so its policy core promotes unchanged when those land. It is strictly read-only — no eMRO writes. The five recommendation types are deterministic v1 precursors; the locked v3/v4/v5 specialists supersede them with ML/agentic versions later.

## Consequences

- The sub-project register grows from 10 to 11; `ROADMAP.md` is amended accordingly. The dated roadmap in `docs/roadmap/` remains the source of truth and is annotated with this amendment.
- The deterministic precursors set expectations and acceptance tests that the later ML/agentic phases must continue to satisfy.
- Two open items are inherited and owned downstream: the §5.5-vs-§6.1 delta-band reconciliation (Guardrail spec, #4) and the data sources for AOG history, repair TAT, and forward scheduled demand (a future extract domain / v2 causal forecasting). See spec §10–§11.
- The engine emits the suggested autonomy tier and guardrail flags but does not enforce them; enforcement remains with the Guardrail & Approval specialist (#4).

## Alternatives considered

- **Adjust-Min/Max only (strict locked v1).** Rejected: the owner wants the full five-type recommendation surface demonstrable now; the deterministic precursors are cheap and high-signal.
- **Full vision now (ML + Bedrock + writeback).** Rejected: pulls forward SageMaker/Bedrock and diverges hardest from the phased roadmap for no near-term benefit — the eight target scenarios are all deterministic.

## References

- Spec: [2026-04-17-trax-io-recommendation-engine-design.md](../superpowers/specs/2026-04-17-trax-io-recommendation-engine-design.md)
- Plan: [2026-04-17-trax-io-recommendation-engine.md](../superpowers/plans/2026-04-17-trax-io-recommendation-engine.md)
- Locked design: [2026-04-14-trax-io-inventory-optimizer-design.md](../design/2026-04-14-trax-io-inventory-optimizer-design.md) §8
