# Trax IO — Steering Committee Brief

**Date:** 2026-04-14
**For:** Trax executive steering committee
**From:** Miguel Sosa, VP Head of Innovation
**Decision sought:** Approval to staff the v1 build (10 sub-projects, 4 build waves, ~7 months to first lighthouse production write).

---

## What we are building

A multi-tenant AI inventory optimization agent for eMRO. Trax IO continuously recomputes `(Re-Order Point, Economic Order Quantity, Safety Stock, Max)` per part per location across every customer's catalog, reacts in real time to flight-plan changes and AD/SB issuance, and writes recommendations back into eMRO under tiered autonomy with full audit and rollback. Built on AWS Bedrock AgentCore (agentic platform), Strands (orchestration), and an explainable deterministic policy engine that planners can sign off on.

## Why now

Three forces converge. (1) Generative-AI-native agents on AWS Bedrock AgentCore are operationally mature in 2026 — the same bet two years ago would have been research-grade. (2) Tier-1 carriers (AC, JetBlue, SIA EC, Lufthansa, WestJet) are actively scoping AI inventory products and the first credible MRO-native vendor wins multi-year contracts. (3) Trax already owns the data — the eMRO catalog, demand history, vendor terms, flight utilization, criticality. Competitors who ship a generic optimizer must integrate; we ship one that already knows the data model.

## Commercial pitch

v1 is "Trax IO Core" — priced per active PN-location under management. Phases 2–6 each ship as a layered SKU (Causal Demand, AOG Shield, Recovery, Sourcing, Network), each independently commercializable. The monthly Business Value Report — productized in v1 — quantifies dollars saved per tenant and is the contract-renewal engine. By v3, the commercial conversation moves from "dollars saved in inventory" to "AOG hours prevented," which is a qualitatively different price tier.

## What v1 specifically delivers

- Continuous tuning of `PN_INVENTORY_LEVEL` per `PN × Location` for every active tenant.
- Hybrid autonomy: aggressive on long-tail expendables, conservative on AOG-critical parts. Planner approval flow with one-click bulk-approve and 90-day rollback.
- Regime-routed ensemble forecasting: Croston/TSB for the long tail, LightGBM with causal covariates for moderate demand, foundation-model challenger in shadow.
- Deterministic policy engine that planners sign off on (the LLM informs decisions; deterministic Python writes them).
- Multi-tenant SaaS in Trax's AWS, with per-tenant KMS and Cedar isolation, SOC 2 Type II certified before lighthouse exits shadow mode.

## Architecture in one sentence

A hierarchical Strands Supervisor on AWS Bedrock AgentCore Runtime delegates to six specialist subagents (Data & Retrieval, Regime Router, Forecasting, Policy Engine, Guardrail & Approval, Writeback) sharing tenant-scoped Memory, Identity, Gateway, and Observability — designed so phases 2–6 each add one specialist without re-architecting the platform.

## Build plan: 10 sub-projects, 4 waves

| Wave | Weeks | Sub-projects | Outcome |
|---|---|---|---|
| 0 | 0–6 | Extract Utility, Feature Store, Observability/SOC 2 | Data flowing; audit plane live |
| 1 | 6–16 | Agent Spine, Forecasting/Policy, Event Publisher, Writeback REST | Full pipeline producing real recommendations |
| 2 | 12–20 | Planner UI, Business Value Report Pipeline | Customer-facing surface and value reporting |
| 3 | 20–24 | Tenant Onboarding | Lighthouse exits shadow mode → Tier B/C writes live |

First production write target: **Week 24.** First SOC 2 Type II attestation: **Week 26.** Second customer signed: **Week 28.**

## Resources required

- 1 AI platform lead + 2 Python engineers + 1 SRE for the Agent Spine.
- 2 ML engineers + 1 ML platform engineer for Forecasting & Policy.
- 1 data engineer + 1 platform engineer for Feature Store & Observability.
- 2 eMRO product engineers for Event Publisher, Writeback REST, and Planner UI (single team, sequential).
- 1 SecOps engineer for SOC 2 Type II.
- 1 customer success engineer for Onboarding (joins week 14).

Approximately **9–10 FTEs for two quarters**, scaling to ~12 with the SOC 2 attestation push.

## Costs and unit economics

Bedrock + SageMaker per-tenant cost during shadow mode runs at roughly $3,000–$8,000/month/tenant depending on catalog size. At commercial pricing of $25K–$60K per tenant per month for Trax IO Core (depending on PN-location count and autonomy tier), v1 gross margin is 70–85% even at low scale. Federated peer-benchmark feature in v2 lifts price by ~30% for adopters and creates a moat that becomes harder to copy with each tenant added.

## What we are not doing in v1 (to prevent scope creep)

Not a chatbot. Not a planning UI replacement. Not a procurement system replacement. Not consuming forward flight plans (v2). Not scoring AOG risk (v3). Not multi-echelon rotable pool sizing (v6). v1 is one well-bounded product surface, not a platform pretending to be one.

## Risks worth surfacing

- **Customer DBA cooperation** for the nightly extract — mitigated by a pre-approved security review package and an alternative AWS DMS path.
- **Long-tail forecast quality** for the `ultra_rare` regime (60–75% of catalog) — mitigated by empirical-Bayes priors borrowed from peer parts and a fallback to current static `PN_INVENTORY_LEVEL` when confidence is low.
- **Planner trust** — measured continuously via override rate and engagement; calibration consulting is part of every onboarding.
- **eMRO release train coordination** — Event Publisher and Writeback REST ride on eMRO releases; we have decoupled them into two separate releases to protect parallel velocity.

## What I am asking the committee for

1. Approval to staff the v1 build with the resource plan above.
2. Designation of a tier-1 lighthouse customer commitment (my recommendation: an existing eMRO customer with a strong inventory practice and a willing CIO sponsor).
3. Approval to begin the SOC 2 Type II audit engagement immediately rather than at v1 completion.
4. Commitment that Event Publisher, Writeback REST, and Planner UI will be sequenced into the next two eMRO product release trains.

## Reading list for the committee

- Design document (10 sections, ~30 pages) — `docs/design/2026-04-14-trax-io-inventory-optimizer-design.md`
- Build roadmap — `docs/roadmap/2026-04-14-trax-io-v1-build-roadmap.md`
- Strands vs LangGraph ADR — `docs/adr/0001-strands-vs-langgraph.md`
- eMRO Event Publisher contract — `docs/contracts/2026-04-14-emro-event-publisher-contract.md`

— Miguel
