# Trax IO v1 — Master Build Roadmap

**Date:** 2026-04-14
**Owner:** Miguel Sosa, VP Head of Innovation
**Status:** Approved
**Source design:** [`docs/design/2026-04-14-trax-io-inventory-optimizer-design.md`](../design/2026-04-14-trax-io-inventory-optimizer-design.md)

---

## Purpose

This roadmap decomposes the v1 Trax IO design into ten independently-shippable sub-projects, sequences them into four build waves, names the owning team for each, identifies the cross-cutting blockers, and frames the lighthouse-customer commitment milestones. It is the orchestration document for the engineering, ML, eMRO product, customer success, and SecOps teams.

---

## Sub-project register

| # | Sub-plan | Plan document | Stack | Owner | Wave | P |
|---|---|---|---|---|---|---|
| 1 | Nightly Extract Utility | `plans/2026-04-14-nightly-extract-utility-plan.md` | Oracle PL/SQL + Python CLI | eMRO team | 0 | P0 |
| 2 | Feature Store & Data Lake | `plans/2026-04-14-feature-store-plan.md` | AWS Glue + Iceberg + DynamoDB | Data platform | 0 | P0 |
| 3 | eMRO Outbound Event Publisher | `plans/2026-04-14-event-publisher-plan.md` + `contracts/2026-04-14-emro-event-publisher-contract.md` | eMRO add-on (Java) + EventBridge + Kinesis | eMRO team + platform | 1 | P1 |
| 4 | Agent Spine | `plans/2026-04-14-agent-spine-implementation-plan.md` | Python + Strands + AgentCore + CDK | AI platform | 1 | P0 |
| 5 | Forecasting & Policy Engine | `plans/2026-04-14-forecasting-policy-plan.md` | Python + SageMaker + statsforecast + LightGBM | ML engineering | 1 | P0 |
| 6 | eMRO Writeback REST API | `plans/2026-04-14-writeback-rest-plan.md` | eMRO Java REST | eMRO team | 1 | P1 |
| 7 | Planner UI "Trax IO Review" | `plans/2026-04-14-planner-ui-plan.md` | eMRO frontend | eMRO team | 2 | P1 |
| 8 | Business Value Report Pipeline | `plans/2026-04-14-bvr-pipeline-plan.md` | Python + WeasyPrint + Glue | ML engineering | 2 | P1 |
| 9 | Observability + SOC 2 Control Plane | `plans/2026-04-14-observability-soc2-plan.md` | OTel + CloudTrail Lake + Audit Manager | Platform + SecOps | 0 (parallel) | P0 |
| 10 | Tenant Onboarding Runbook | `plans/2026-04-14-tenant-onboarding-runbook.md` | Operational playbook + scripts | Customer success + ML | 3 | P1 |

---

## Wave plan

### Wave 0 — Foundation (start day one, parallel)

Sub-projects: **#1 Nightly Extract**, **#2 Feature Store**, **#9 Observability + SOC 2 control plane**.

Wave 0 builds the bones. Without these, nothing else can run. The Extract Utility produces tenant data; the Feature Store lands and serves it; the Observability/SOC 2 plane is started now because retroactive SOC 2 evidence is impossible — every CloudTrail event from day one matters for the first attestation.

**Exit criteria:** First lighthouse tenant's nightly extract running clean for 14 days; Feature Store Iceberg tables backfilled and queryable for that tenant; CloudTrail Lake event store live with seven-year retention; AWS Audit Manager SOC 2 framework attached.

**Duration target:** 6 weeks.

### Wave 1 — Core agent + write path

Sub-projects: **#4 Agent Spine**, **#5 Forecasting & Policy**, **#3 Event Publisher**, **#6 Writeback REST**.

Wave 1 turns data into decisions. Agent Spine runs end-to-end against the Feature Store from Wave 0, with stub Forecasting/Policy until #5 is ready. #5 replaces the stubs with the real model stack. #6 ships the eMRO Writeback REST API so the agent can write recommendations. #3 ships the Outbound Event Publisher so the event lane can fire on schedule changes, AD/SB issuance, and stock movements.

**Exit criteria:** Agent Spine produces real recommendations against real lighthouse tenant data; Forecasting champion/challenger evaluation pipeline running nightly; eMRO Writeback REST deployed in lighthouse customer's eMRO instance; first end-to-end shadow-mode write against `fake_emro` and against the real eMRO endpoint.

**Duration target:** 10 weeks (overlapping the tail of Wave 0).

### Wave 2 — UI + value reporting

Sub-projects: **#7 Planner UI**, **#8 Business Value Report Pipeline**.

Wave 2 turns recommendations into a planner-facing product and a customer-facing value story. The Planner UI is what the customer's planning lead actually opens every morning. The BVR pipeline is what the customer's CFO sees once a month and what justifies contract renewal.

**Exit criteria:** Planner UI embedded in lighthouse eMRO instance; planner can review, approve, reject, defer, and bulk-approve recommendations; first month of Business Value Report PDF auto-generated and posted to Planner UI.

**Duration target:** 8 weeks (overlapping Wave 1).

### Wave 3 — Go-live

Sub-projects: **#10 Tenant Onboarding Runbook**.

Wave 3 shepherds the lighthouse customer from shadow → canary → tiered autonomy. The Onboarding Runbook codifies essentiality mapping, service-level target calibration, autonomy band tuning, kill-switch ownership, and the 90-day shadow-mode protocol.

**Exit criteria:** Lighthouse tenant exits shadow mode; first Tier B/C writes flowing into production; second tenant signed and onboarding queued.

**Duration target:** 4 weeks.

---

## Critical-path dependency map

```
#1 Extract Utility ──┬─► #2 Feature Store ──┬─► #4 Agent Spine ──┬─► #6 Writeback REST API
                     │                      │                    │
                     │                      │                    └─► #5 Forecasting & Policy
                     │                      │
                     │                      └─► #8 BVR Pipeline
                     │
                     └─► #9 Observability + SOC 2 (parallel from day one)

#3 Event Publisher ──► #4 event lane (P1, not on critical path for first shadow run)

#7 Planner UI ──► requires #6 Writeback REST API
#10 Onboarding ──► requires #4, #5, #7, #8
```

**The single most load-bearing item is #2 Feature Store.** If it slips, everything downstream slips. Staff it appropriately and treat its Phase 1 (Iceberg schema + Glue jobs ingesting #1's nightly extracts) as a hard gate.

---

## Cross-cutting concerns

**SOC 2 Type II.** Sub-plan #9 owns the control plane, but every other sub-plan has SOC 2 hooks (CloudTrail tagging, KMS envelope encryption, audit-log emission, access-review evidence). #9's onboarding doc for engineers is mandatory reading before touching any other sub-plan.

**Tenant isolation.** Enforced at four layers: contract (`TenantContext` propagation), agent (`Specialist._assert_tenant_match`), data (Feature Store namespace + KMS), and infrastructure (per-tenant CDK stacks). Every PR in every sub-plan should be reviewable against this checklist.

**Federated cross-tenant learning.** Lit up in v2; in v1 the infrastructure (de-identified feature pipeline + separate training account) is in place but only champion models per tenant ship. Sub-plan #5 carries the v1 scaffolding.

**The eMRO release train.** Sub-plans #3, #6, and #7 ship inside one or two eMRO product releases. Coordinate with the eMRO product manager on which release carries the Trax IO surface — and decide early whether it ships as a feature flag or as a separate eMRO module.

---

## Lighthouse customer milestones

| Week | Milestone | Owner | Status |
|---|---|---|---|
| 0 | Lighthouse customer signed for shadow-mode pilot | Sales + Innovation | Pending |
| 2 | Customer's nightly extract runs clean against pilot dataset | eMRO team + customer DBA | — |
| 4 | Feature Store backfilled with 24 months of customer history | Data platform | — |
| 8 | First Agent Spine recommendation produced (stub forecaster) | AI platform | — |
| 12 | Real Forecasting & Policy stack producing recommendations | ML engineering | — |
| 14 | Planner UI deployed in customer's eMRO; planner uses it to review | eMRO team + customer planner lead | — |
| 16 | Shadow-mode telemetry: agent recommendations vs. planner decisions | ML engineering | — |
| 20 | First Business Value Report delivered | ML engineering | — |
| 24 | First production Tier B/C writes (non-critical expendables) | Customer success | — |
| 26 | First SOC 2 Type II attestation complete | SecOps | — |
| 28 | Second customer signed; v1.1 scope locked | Sales + Innovation | — |

---

## **v1 Completion Status (as of 2026-07-13)**

**All 10 sub-projects COMPLETE and verified.** v1 is running locally in Docker.

| # | Sub-project | Status | Details |
|---|---|---|---|
| 1 | Nightly Extract Utility | ✅ Complete | Oracle PL/SQL queries packaged; sample extract runs in tests |
| 2 | Feature Store & Data Lake | ✅ Complete | Iceberg tables + DynamoDB online layer + pooling engine live |
| 3 | eMRO Event Publisher | ✅ Complete | Canonical event schema frozen; Kafka routing live |
| 4 | Agent Spine | ✅ Complete | Supervisor + Specialists orchestration; 266 tests passing |
| 5 | Forecasting & Policy Engine | ✅ Complete | Statistical/Gradient-Boosted/Empirical-Bayes projectors; regime routing |
| 6 | eMRO Writeback REST API | ✅ Complete | Java/Quarkus Slice 2; rollback/history ledger + Kafka domain routing |
| 7 | Planner UI (React frontend) | ✅ Complete | 282 Vitest tests; all feature-parity waves done (CSV export, history, BVR, dark/light theme) |
| 8 | Business Value Report Pipeline | ✅ Complete | JSON/HTML/PDF reports; savings attribution + governance disclosures |
| 9 | Observability + SOC 2 | ✅ Complete | CloudTrail Lake + Audit Manager framework; OTel instrumentation |
| 10 | Tenant Onboarding Runbook | ⏳ **Next** | Design ready; scopes shadow→canary→tiered autonomy transition |

---

## **v2–v6 Platform Roadmap**

The v1 design gates v2–v6 as independently-shippable specialists, each adding one capability without re-architecting the Supervisor.

### v2 — Causal Demand Forecasting
**Commercial SKU:** "Trax IO Causal" | **Owner:** ML engineering

- Ingests forward flight plans from OCC/commercial scheduling (new extract path)
- Replaces v1's historical-baseline forecasting with forward-looking demand distributions
- Federated peer-benchmark feature (premium) — cross-tenant anonymized learning
- Unlocks better ROP for flying-program-tied parts

**Blocker:** OCC system integration scoping must happen before coding starts (identify source system, contract fields, backfill strategy).

**Pre-work:** Add 2–3 new extract queries for forward-plan ingestion; define featurization layer for AC-type × route × day demand.

---

### v3 — AOG & Shortage Risk Agent
**Commercial SKU:** "Trax IO AOG Shield" | **Owner:** AI platform (Strands specialist)

- Predicts stockouts N days forward; scores AOG risk per tail
- Recommends expedites, transfers, interchangeable substitutions, vendor switches
- Tier A only in v3 (hard guardrails)
- Shifts commercial narrative from "dollars saved" to "AOG hours prevented"

**Blocker:** Per-tenant AOG cost model calibration (consulting engagement per customer until v3.5 automation lands).

**Architecture:** New Strands `Agent` in Supervisor; integrates v2's forecast + v1's policy engine + open-order/vendor-performance signals.

---

### v4 — Excess, Obsolete & Redistribution
**Commercial SKU:** "Trax IO Recovery" (variable revenue component) | **Owner:** ML engineering

- Detects slow-movers, idle rotable pool inflation, shelf-life-expiring inventory
- Recommends redistribution between stations, return-to-vendor, core exchange, phase-out, third-party sale
- **First phase to use AgentCore Code Interpreter** (planner-driven scenario math)

**Depends on:** v1 (baseline) + v4 demand signal (`CUSTOMER_ORDER_*` deferred from v1).

---

### v5 — Repair-vs-Buy Sourcing Optimizer
**Commercial SKU:** "Trax IO Sourcing" | **Owner:** ML engineering

- For each demand event (actual or forecast), recommends optimal route:
  - New purchase order (PO)
  - Repair return order (RO)
  - Interchange
  - Rental
  - Loan
  - Pool exchange
  - Cannibalization
- Uses `PN_VENDOR_PRICE`, repair cost, criticality, open orders, v3 AOG urgency
- LightGBM route classifier

**Depends on:** v2 (forecasts) + v3 (AOG urgency signals).

---

### v6 — Rotable Pool Sizing (Multi-Echelon METRIC)
**Commercial SKU:** "Trax IO Network" (premium tier, multi-year contract anchor) | **Owner:** Data platform

- Full multi-echelon optimization across main ↔ outstation hierarchy
- Uses METRIC/VARI-METRIC with realistic TAT distributions, interchangeability groups, cannibalization policy, fleet plan
- Discrete-event simulator (custom, SageMaker-hosted)
- **The phase that makes Trax uncatchable by SAP/Ramco/IFS in the IM category**

**Blocker:** METRIC/VARI-METRIC research + discrete-event simulator engineering (2–3 quarter effort).

**Integration:** SageMaker job invoked by Rotable Pool specialist's tool surface (not a Strands agent).

---

## **v2–v6 Priority & Sequencing**

1. **v2 first** — Blocks nothing; unlocks better baseline forecasts for all downstream phases. Pre-work: 4 weeks (OCC scoping + extract design).
2. **v3 & v5 in parallel** — v5 consumes v3 signals but doesn't block v3. Sourcing engineering can start as v3 ramps.
3. **v4 after v2** — Needs the forward demand signal.
4. **v6 last** — Multi-quarter research + engineering; premium tier, not blocking earlier revenue.

---

## **Known Constraints & Research Items**

| Item | Status | Owner | Note |
|---|---|---|---|
| OCC forward-plan integration (v2 pre-work) | ⏳ Pending | Customer success + Innovation | Identify source system, contract fields, backfill approach |
| AOG cost model automation (v3.5) | 🔬 Research | ML engineering | Per-tenant calibration is consulting today; auto-calibration is a learnable model |
| METRIC/VARI-METRIC simulator (v6) | 🔬 Research | Data platform | Custom discrete-event simulator; SageMaker container orchestration |
| Federated cross-tenant learning (v2 infra) | ✅ Designed | ML engineering | De-identified feature pipeline + separate training account; v1 infra in place, product-surface in v2 |
| Code Interpreter hardening (v4) | ⏳ Design | AI platform + SecOps | Scope the tool boundaries, prompt injection mitigations, audit trail |

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Customer DBA refuses Trax-signed extract utility | Medium | High (#1 blocked) | Ship pre-approved security review package; offer DMS path as alternative |
| Forward flight plan ingestion (v2) blocked because OCC system is opaque | High | Medium (v2 only) | Begin OCC integration scoping at Wave 1; identify customer ops contact early |
| Bedrock cost ceiling breached at multi-tenant scale | Medium | High | Per-tenant cost ledger from day one; commercial contract includes overage pricing |
| Long-tail forecast quality regresses on `ultra_rare` regime | High | High | Empirical-Bayes priors + foundation-model challenger; v1 falls back to static `PN_INVENTORY_LEVEL` when forecast confidence is low |
| Planner trust collapses (high override rate, low engagement) | Medium | High | Trust-score telemetry surfaced in observability; sub-plan #10 includes calibration consulting |
| eMRO release train slips and #3/#6/#7 miss the window | Medium | High | Two-release-train plan: #3 (event publisher) and #6/#7 (REST + UI) decoupled into separate releases |
| Prompt injection via `REMOVAL_REASON` free-text fields | Medium | Medium | Defensive prompt construction; strict tool-use scopes; v1 red-team suite includes injection payloads |
| Lighthouse customer departs before Tier B/C goes live | Low | Critical | Monthly Business Value Report from week 20 anchors retention; second tenant target by week 28 |

---

## Decision log (ADRs)

The architecturally-significant decisions below have dedicated ADRs:

- [`adr/0001-strands-vs-langgraph.md`](../adr/0001-strands-vs-langgraph.md) — Supervisor framework choice
- [`adr/0002-in-memory-feature-store-stub.md`](../adr/0002-in-memory-feature-store-stub.md) — Spine/Feature-Store decoupling
- [`adr/0003-fake-emro-contract-testing.md`](../adr/0003-fake-emro-contract-testing.md) — eMRO writeback contract testing strategy

Future ADRs (as decisions arise):
- ADR-0004 — Federated cross-tenant feature pipeline isolation model
- ADR-0005 — AOG cost model calibration methodology
- ADR-0006 — Multi-echelon (METRIC) simulator architecture (v6)

---

## Reading order for new team members

1. The Design — `docs/design/2026-04-14-trax-io-inventory-optimizer-design.md`
2. This Roadmap
3. Their sub-plan
4. The relevant ADRs
5. The eMRO Event Publisher integration contract (everyone, even non-eMRO teams)
6. The SOC 2 onboarding section in sub-plan #9
