# Trax IO — Inventory Optimization Agent for eMRO

**Design document**
**Date:** 2026-04-14
**Owner:** Miguel Sosa, VP Head of Innovation, Trax
**Status:** Approved design, ready for implementation planning
**Classification:** Trax internal

---

## 1. Executive summary

Trax IO is a multi-tenant, AI-driven inventory optimization agent layered on the Trax eMRO product. Version 1 targets the single highest-leverage decision in airline spares management: continuously recomputing `(ROP, EOQ, Safety Stock, Max)` per `PN × Location` under tiered autonomy, replacing the static values in `PN_INVENTORY_LEVEL` with policy-driven ones that react to utilization, wash rate drift, lead-time variance, and part criticality.

The product is built on AWS Bedrock AgentCore with a hierarchical agent topology orchestrated by AWS Strands, a regime-routed ensemble forecasting stack, and a deterministic, non-LLM policy engine that produces the values written back to eMRO. The v1 design anticipates five subsequent phases (causal demand forecasting, AOG and shortage risk, excess and redistribution, repair-vs-buy sourcing, multi-echelon rotable pool sizing) and is explicitly architected so each phase adds one specialist subagent to the existing spine without re-architecting the platform.

Commercial posture is multi-tenant SaaS hosted in Trax's AWS, with tenant isolation enforced through per-tenant KMS keys, Cedar authorization policies, AgentCore Memory namespaces, and IAM role boundaries. SOC 2 Type II is baked into the control plane from day one. A monthly Business Value Report per tenant, productized in v1, is the contract-renewal engine and the mechanism that prevents the product from being perceived as a pilot that can be unplugged.

---

## 2. Design decisions (grilled and locked)

The following eight decisions anchor the architecture. Each was chosen after explicit trade-off review; alternatives are noted where relevant for future revisits.

| # | Decision | Chosen | Rationale |
|---|---|---|---|
| Q1 | v1 mission | Dynamic stock-level tuning; phases 2–6 on roadmap | Highest-leverage single decision, cleanest explainability, ships fastest |
| Q2 | Autonomy posture | Hybrid by part class (D) | Aggressive on long-tail expendables, conservative on AOG-critical; captures value fast while managing blast radius |
| Q3 | Tenancy model | Multi-tenant SaaS in Trax AWS (B) | Fastest onboarding and Trax commercial upside; isolation enforced via KMS, Cedar, and AgentCore Memory namespaces |
| Q4 | Objective function | Staged: v1 service-level-constrained cost minimization; v2 AOG-cost minimization; v3+ multi-echelon METRIC (D) | Ship explainable math first; architect feature layer to carry future objectives |
| Q5 | Agent topology | Hierarchical from day 1, Strands-based Supervisor (B) | ~30% up-front investment, zero refactor cost at phases 2–6 |
| Q6 | Recompute cadence | Tiered: nightly full-fleet + hourly hot-parts + event-triggered (C) | Batch economics on the long tail, real-time responsiveness where it matters |
| Q7 | Ingestion mechanism | Hybrid: daily S3 extract + eMRO outbound event publisher + optional DMS/CDC (E) | Decouples shipping the agent from shipping the eMRO event publisher release |
| Q8 | Model stack | Regime-routed ensemble + deterministic policy layer (D) | Survives honest aviation long-tail data; gives planners a classical fallback and Trax a defensible moat |

Additional locked items: Strands over LangGraph for the Supervisor; Policy Engine and Writeback are non-LLM Python; SOC 2 Type II control pipeline is in scope from v1; the 12 SQLs provided are the complete v1 ingestion contract; a 7th domain event `eo_published` is added to the outbound event publisher; REST Writeback API is the write surface (not a stored procedure); Business Value Report is productized in v1.

---

## 3. High-level architecture

### 3.1 Agent topology (Bedrock AgentCore)

A single **Supervisor agent** built on AWS Strands, deployed on AgentCore Runtime, owns tenant context, session state, approval routing, and orchestration. The Supervisor never performs domain work itself. It dispatches to six specialist subagents, each an independently-versioned AgentCore Runtime service:

1. **Data & Retrieval Agent** — the only component that touches the feature store, eMRO extracts, and the event lane. All other agents read through it. One chokepoint for tenant isolation, query optimization, and caching.
2. **Regime Router Agent** — for each `PN × Location × Interchangeability-Group`, classifies into `ultra_rare | intermittent | moderate | high_volume`, selects the forecasting model, and emits the forecast request. Rule-based; LLM used only to explain regime transitions in the Planner UI.
3. **Forecasting Agent** — hosts the regime-specific models (classical intermittent, LightGBM, foundation-model challenger) behind a uniform distribution-returning interface. Delegates to SageMaker for inference.
4. **Policy Engine Agent** — deterministic Python, no LLM. Takes `(demand_distribution, lead_time_distribution, service_level_target, cost_structure, hard_constraints)` and returns `(ROP, EOQ, SS, Max)` with full provenance. This is the layer planners sign off on.
5. **Guardrail & Approval Agent** — implements the three-tier autonomy model, enforces Cedar policies, routes out-of-band recommendations to human approval queues, logs every decision. Backed by AgentCore Identity and Cedar.
6. **Writeback Agent** — the only agent with write permission into eMRO. Writes approved `PN_INVENTORY_LEVEL` changes, logs the delta to `PN_INVENTORY_LEVEL_HISTORY`, and handles rollback. Strictly scoped IAM role.

Phases 2–6 add one specialist each (Causal Demand Forecaster, AOG Risk, Excess & Redistribution, Sourcing, Rotable Pool) without touching the Supervisor's contract. This is the payoff for the hierarchical-from-day-1 investment.

### 3.2 Shared infrastructure

**AgentCore Memory** holds tenant-scoped long-term memory for planner feedback, model performance history, and policy-band overrides. **AgentCore Gateway** exposes MCP tools for read/write into eMRO: `get_demand_history`, `get_stock_position`, `get_vendor_terms`, `propose_stock_level`, `commit_stock_level`, `open_approval_task`. **AgentCore Identity** propagates tenant and user context; every tool call carries identity for Cedar evaluation. **AgentCore Observability** emits OpenTelemetry traces per agent hop with per-tenant cost attribution. AgentCore Browser and Code Interpreter are not used in v1; Code Interpreter arrives with phase 4 (Excess & Redistribution) for planner-driven scenario math.

### 3.3 Foundation model strategy

Claude Sonnet 4.6 is the default reasoning model for the Supervisor, Regime Router, and Guardrail agents. Claude Haiku 4.5 handles high-volume Data & Retrieval routing where reasoning is light. No LLM is in the Policy Engine or Writeback path; deterministic Python handles both.

---

## 4. Data plane

### 4.1 Ingestion paths

**Nightly path (workhorse).** Each tenant lands a daily Oracle Data Pump / SQL\*Plus extract into `s3://trax-io-landing/<tenant_id>/YYYY/MM/DD/`. The extracts materialize the twelve queries provided: `CausalValues`, `DemandHistoryRotables` (rotable removals and expendable issues), `Events`, `LocationMaster`, `OrderPlan` (open + closed + requisition), `PartChain` with details, `PartMaster`, `PartLocation`, `PnVendorPrice`, `SalesOrder`, `StockAmount`, `StockLevelUpload`, `TransCode`, `Vendor`. Queries are hardened with bind variables and parameterized date windows and packaged as a Trax-signed extract utility that the customer DBA runs nightly on their existing scheduler. S3 lands to AWS Glue, which writes Apache Iceberg tables in the Trax data lake partitioned by `tenant_id` and `extract_date`.

**Event path.** The **eMRO Outbound Event Publisher** ships as a Trax-authored add-on inside a dedicated eMRO release and emits seven domain events over HTTPS/mTLS to the Trax IO event endpoint: `flight_completed`, `stock_moved`, `wo_scheduled`, `vendor_price_changed`, `plan_published`, `removal_recorded`, `eo_published`. Events land on Amazon EventBridge, then fan out to per-tenant Kinesis streams for Iceberg CDC materialization and to Step Functions for hot-parts and event-triggered recompute via AgentCore Runtime invocation. The event schema carries a `schema_version` field and is semver-governed; breaking changes will force every customer to upgrade, so the schema is contract-first and over-engineered now.

**Optional DMS/CDC path.** Packaged as an upsell for tier-1 customers who want hourly hot-parts responsiveness without waiting for the event-publisher eMRO release. AWS DMS reads Oracle redo logs from a customer-carved read-only replica and lands to the same Iceberg tables.

### 4.2 Feature store

Offline features live as versioned Iceberg tables in the Trax data lake, recomputed nightly by Glue jobs, one table per feature group (`demand_history`, `causal_utilization`, `lead_time_distribution`, `wash_rate_history`, `vendor_economics`, `part_attributes`, `criticality`, `interchangeable_graph`, `location_graph`, `open_orders_snapshot`). Partitioned by `tenant_id` with Iceberg time-travel enabled so any historical recommendation can be reproduced for audit — a SOC 2 requirement and a planner-trust multiplier. Online features live in a thin DynamoDB layer keyed on `(tenant_id, pn, location)`, populated by nightly Glue and updated incrementally by the event lane. Sub-10ms reads for event-triggered inference. SageMaker Feature Store is deliberately avoided; the read pattern does not justify the cost.

### 4.3 Feature semantics (v1)

Derived directly from the 12 v1 SQLs:

**Demand intensity.** Removals (rotable) plus issues (expendable) per `PN × Location`, rolled to day/week/month. Sourced from `AC_PN_TRANSACTION_HISTORY` and `PN_INVENTORY_HISTORY`.

**Causal utilization.** Flight hours and cycles by AC type × destination × day, from `AC_ACTUAL_FLIGHTS` joined to `AC_MASTER`, linked to demand via `ac → ac_type`.

**Wash rate.** Computed nightly per the `PartMaster` SQL formula `(RO/CREATE − RO/RECEIVING) / RO/CREATE`; we store the trend, not just the point value.

**Lead-time distribution.** Empirical from `PN_VENDOR_PRICE.lead_days` combined with realized `ActualRcvDate − PlanOrderDate` from closed orders. The promised-vs-actual delta is one of the highest-signal drivers of safety stock and is treated as a first-class feature.

**Interchangeability graph.** From `PN_INTERCHANGEABLE` and `pn_interchg_one_way`. Demand and stock are rolled to the interchange group (honoring one-way chains) before the policy calculation; otherwise stock is over-sized.

**Location hierarchy.** From `LOCATION_MASTER.RELATED_MAIN_WAREHOUSE`. Main warehouses get base-stock sized for aggregate fleet demand; outstations get emergency levels sized for local AOG risk plus transfer lead time. This is proto-multi-echelon in v1; full METRIC arrives in phase 6.

**Criticality.** `PN_MASTER.ESSENTIALITY_CODE` mapped through `SYSTEM_TRAN_CODE` where `SYSTEM_TRANSACTION = 'ESSENTIALITY'`, routed through a **per-tenant Essentiality Mapping Table** that normalizes each customer's codes to the canonical five-tier scale (defaults auto-inferred from code descriptions during onboarding; planner can override).

**Cost economics.** `pm.MARKET_VALUE_UNIT_COST`, `pm.AVERAGE_COST`, `PKG_TRAX_PTC.getKitCost`, and 24-month average `RepairCost` from `ORDER_INVOICE`.

**Fleet effectivity.** `PN_EFFECTIVITY_HEADER` and `PN_EFFECTIVITY_DISTRIBUTION.NoOfTails`. Acts as a demand scaling factor when fleet size changes.

**Hard constraints.** `SHELF_LIFE_DAYS`, `HAZARDOUS_MATERIAL`, `TOOL_CONTROL_ITEM` — these cap Max Stock, they are not optimization targets.

### 4.4 v1 exclusions (YAGNI)

Third-party sales demand (`CUSTOMER_ORDER_*`) is treated as noise in v1 and modeled explicitly in phase 4. Rotable loan pool (`LOAN_CATEGORY` in `PN_INVENTORY_DETAIL`) is deferred to phase 6. Forecasted forward flight plans are not ingested in v1 — v1 uses historical causal intensity as the projection baseline; v2 replaces this with forward plans from OCC/commercial scheduling.

### 4.5 SOC 2 Type II data-plane hooks

Every ingress logged to an immutable audit bucket (S3 Object Lock, Compliance mode). Glue lineage captured via OpenLineage. KMS envelope encryption is tenant-scoped with annual rotation. Access to the landing bucket, feature store, and event endpoint is Cedar-governed with all attempts logged to CloudTrail Lake at seven-year retention. Quarterly control tests are automated via AWS Config and Audit Manager so the annual SOC 2 audit is an evidence review, not a scramble.

---

## 5. Model stack and policy engine

### 5.1 Regime router

Nightly and on event-lane triggers, every `PN × Location × Interchangeability-Group` is classified into one of four regimes:

- **`ultra_rare`** — fewer than 6 removals in 24 months, or new PN with less than 90 days of history. Typically 60–75% of catalog by count, under 10% by demand volume.
- **`intermittent`** — 6–24 removals in 24 months. Typically 15–25% of catalog.
- **`moderate`** — 25–200 removals in 24 months. Typically 5–10% of catalog.
- **`high_volume`** — 200+ removals in 24 months. Typically 1–5% by count, majority of demand volume.

Router output is stored on the `PN × Location` record and only re-classified when demand density crosses a hysteresis band (±20%) to prevent flapping.

### 5.2 Forecasting models per regime

| Regime | Champion model | Challenger (shadow) |
|---|---|---|
| `ultra_rare` | Compound-Poisson with empirical-Bayes priors from peer PNs (same ATA, criticality, fleet) | Chronos / Moirai zero-shot |
| `intermittent` | Croston / TSB / SBA, auto-selected via Kourentzes/Syntetos classification | LightGBM with causal covariates |
| `moderate` | LightGBM with causal covariates | TSB as guardrail fallback |
| `high_volume` | LightGBM + foundation-model ensemble (weighted by rolling MAPE) | Raw foundation model |

All models return a demand distribution (or at minimum mean, variance, and tail percentiles), not a point forecast. The policy engine requires the distribution because service-level math is stochastic.

### 5.3 Cross-tenant federated learning

LightGBM and foundation-model challengers are trained on de-identified, PN-level features (wash rate, causal intensity, criticality, ATA, fleet type). No tenant identifiers, part numbers, or vendor identities cross the tenant boundary. Federated training runs in a separate AWS account with no read access to tenant PII. Tenant contracts explicitly authorize de-identified feature contribution; benchmarks return as the "peer median" premium feature, packaged and surfaced as a product in phase 2 once tenant count justifies it.

### 5.4 Policy engine

Deterministic, pure Python, no LLM. Algorithm selection by regime:

**Ultra-rare + high-criticality parts (essentiality tier 1–2)** use one-for-one base-stock `(S−1, S)` policy with service-level target driven by the AOG cost model. **Intermittent** parts use `(s, S)` continuous-review with `s = ROP`, `S = ROP + EOQ`. EOQ uses Wilson's formula adjusted for lead-time-demand distribution; safety stock is `SS = z_α · σ_LTD`. **Moderate and high-volume** parts use `(R, Q)` periodic-review aligned to the vendor review cycle, with `MINIMUM_ORDER_QTY` from `PN_VENDOR_PRICE` as a hard floor.

Interchangeability rollup is always applied before the policy calc; one-way chains are honored. Shelf-life, hazmat, and tool-control flags act as hard caps on Max Stock. Location hierarchy is honored as described in §4.3.

Every Policy Engine output includes a provenance record: which model produced the forecast, the distribution parameters, the service-level target, the binding constraints, and the delta versus current `PN_INVENTORY_LEVEL`. The provenance surfaces in the Planner UI and the SOC 2 audit trail.

### 5.5 Service-level targets (v1 defaults, tenant-overridable)

| Essentiality tier | Fill-rate target | Max out-of-band without approval |
|---|---|---|
| 1 (AOG / NO-GO / flight-safety) | 99.5% | 0% (always planner approval) |
| 2 (GO-IF) | 98% | ±5% delta from current ROP |
| 3 (dispatch-critical rotable) | 95% | ±15% delta |
| 4 (routine expendable) | 92% | ±25% delta |
| 5 (consumable, non-critical) | 90% | ±40% delta |

A lighthouse-customer onboarding deliverable calibrates these targets from the tenant's actual AOG history; this calibration becomes a consulting engagement that justifies premium commercial tiers.

### 5.6 Champion/challenger promotion

Models are scored nightly on a rolling 60-day holdout against three metrics: cost-and-criticality-weighted MAPE, realized vs. targeted fill rate, and total cost (holding + ordering + stockout proxy). A challenger is auto-promoted only after beating the champion on all three for 45 consecutive days, preceded by a planner-visible change notice. No silent model swaps.

---

## 6. Autonomy, guardrails, and the eMRO integration surface

### 6.1 Three autonomy tiers (Cedar-policy-enforced)

**Tier A — Advisor only.** Recommendation lands in the eMRO review queue; planner reviews and approves; Writeback Agent writes. Default for essentiality tier 1, unit cost ≥ $10K, delta > 25%, first 90 days of tenant deployment, and any PN under active phase-3 AOG investigation.

**Tier B — Bounded autonomy.** Agent writes within policy bands; out-of-band recommendations fall back to Tier A. Default for essentiality tier 2–3, delta within ±15%, unit cost < $10K, no active investigation. Tier B writes generate a 14-day visible planner notification with one-click rollback.

**Tier C — Autonomous with diff report.** Agent writes; planner sees a weekly digest. Default for essentiality tier 4–5 expendables/consumables, unit cost < $500, high-volume regime, delta within ±40%. This covers the 60–70% of catalog by count where most savings live.

### 6.2 Hard guardrails (never bypassed)

- Single-write delta on `ROP`, `EOQ`, or `Max` is capped at 100%, even in Tier C.
- Floor constraints: `SS ≥ 0`, `ROP ≥ SS`, `Max ≥ ROP + EOQ`, `EOQ ≥ MinOQ`.
- Shelf-life clamp: `Max × avg_daily_demand ≤ 0.6 × SHELF_LIFE_DAYS`.
- Hazmat and tool-control parts: `Max` cannot increase more than 2× per write cycle.
- If open POs/ROs would cause stock to exceed proposed Max, write is deferred one cycle.
- Active AOG case or shortage event in the last 72h on the part or tail forces Tier A regardless of other criteria.
- Per-tenant kill switch reverts the agent to shadow mode within 60 seconds; global Trax kill switch covers all tenants within 5 minutes.

### 6.3 eMRO integration surface (four contracts)

1. **Nightly extract contract** — the 12 SQLs packaged as a signed Trax utility running on the customer's Oracle scheduler, uploading via presigned URL.
2. **Outbound Event Publisher** — seven domain events over HTTPS/mTLS, shipped as an eMRO add-on module, schema versioned.
3. **Writeback REST API** — a new REST surface inside eMRO, authenticated by Trax service principal, scoped strictly to `PN_INVENTORY_LEVEL` rows. Transactional writes logged to `PN_INVENTORY_LEVEL_HISTORY` with `(old_value, new_value, changed_by_agent, agent_version, forecast_provenance_id, tier, timestamp)`. This history table is also the rollback surface.
4. **Planner UI "Trax IO Review"** — embedded module in eMRO: pending recommendations with provenance, one-click approve/reject/defer, bulk-approve by filter, weekly Tier-C digest, kill switch, essentiality mapping config, service-level target config.

Writeback is the only agent with `PN_INVENTORY_LEVEL:Write` IAM permission. Every other agent is read-only.

### 6.4 Approval routing and rollback

Approval tasks are priority-ordered by `criticality × cost × |delta|`, stored in DynamoDB, and queried by the Planner UI. Default auto-expire is 14 days; unreviewed recommendations archive and flag for the next nightly recompute. Bulk-approve by filter is first-class. Every write is reversible for 90 days via `PN_INVENTORY_LEVEL_HISTORY`; bulk rollback requires a second planner confirmation. The 90-day rollback window is configurable but cannot be set to zero.

---

## 7. Observability, evaluation, and safety

### 7.1 Operational observability

AgentCore Observability emits OpenTelemetry traces to AWS X-Ray tagged with tenant, session, decision ID, and cost attribution (input/output/cached tokens per model). Structured logs stream to CloudWatch and OpenSearch with tenant isolation at the log-group level. Metrics publish to CloudWatch and Managed Grafana: nightly recompute duration, event-lane latency (p50/p95/p99), recommendations generated and applied/rejected/deferred, model inference latency, tool-call failure rates, per-tenant Bedrock spend. SLOs with burn-rate alerts: nightly recompute within 6 hours, event-lane decision within 15 minutes p95, writeback success rate ≥ 99.9%.

### 7.2 Business / model observability

A dedicated Evaluation Pipeline on SageMaker and Glue scores every recommendation on a rolling basis across forecast accuracy (weighted MAPE, bias, interval coverage), policy accuracy (realized vs. target fill rate per essentiality tier, stockout count and cost, excess carrying cost), savings attribution (holding cost delta, AOG-event delta, expedite-order delta, ordering-frequency delta, all against a counterfactual baseline from the tenant's pre-agent history), planner override rate per regime, and a composite planner trust score.

### 7.3 Monthly Business Value Report (productized in v1)

Auto-generated per tenant each month, rendered as a Trax-branded PDF and posted to the Planner UI. Report schema, savings-attribution methodology, and Planner-UI delivery mechanism are v1 scope. This is the contract-renewal engine and the artifact that distinguishes Trax IO as a product rather than a pilot.

### 7.4 Red-team, shadow mode, and canary

Shadow mode runs 30–90 days on every new tenant (Tier A forced); the Evaluation Pipeline scores the agent against the tenant's actual planner decisions and emits a weekly "what the agent would have recommended" report. Canary cohort then scopes first production writes to a single station, tier-4 expendables only, Tier B, for 30 days before expansion. A red-team evaluation suite — maintained by Trax ML ops — regression-tests every model and agent release against adversarial scenarios (fleet-wide wash-rate spike, vendor lead-time collapse, AD-driven demand shock, seasonal demand inversion, zero-demand parts with phantom historical signal).

### 7.5 SOC 2 Type II audit pipeline

All writebacks logged to the in-eMRO `PN_INVENTORY_LEVEL_HISTORY` table and mirrored to an immutable S3 bucket (Object Lock, Compliance mode, 7-year retention) in a separate Trax audit account. Dual-book so neither party can unilaterally rewrite history. Every Planner-UI decision emits a signed audit event. AgentCore Identity events stream to CloudTrail Lake with 7-year retention. AWS Audit Manager runs a pre-built SOC 2 framework continuously collecting evidence. Every model version in SageMaker Model Registry with full lineage to training dataset hash, training-job hash, approver, and champion/challenger evaluation record — documentation-compatible with EU AI Act high-risk-system templates.

### 7.6 Incident response

Runbooks exist for five incident classes: model-produced garbage, writeback failure, event-lane stall, tenant data leakage, and LLM prompt-injection (free-text fields such as `REMOVAL_REASON` are treated as an injection surface). Kill switches pause tenant or global agent activity in ≤60s and ≤5min respectively. Rollback is 90 days. Post-incident review template and 30-day action SLA apply.

### 7.7 Cost observability

Per-tenant cost is tagged on every LLM invocation and SageMaker inference, aggregated nightly, and surfaced in the Planner UI ("your tenant's Trax IO cost this month: $X"). Anomaly alerts fire before the monthly bill surprises anyone. This data also informs commercial pricing tiers.

---

## 8. Phased roadmap

### v1 — Dynamic Stock-Level Tuning *(this design)*

Two-quarter target from plan approval to lighthouse pilot, three quarters to second customer. Specialists shipped: Data & Retrieval, Regime Router, Forecasting, Policy Engine, Guardrail & Approval, Writeback. Value: continuous `(ROP, EOQ, SS, Max)` tuning under tiered autonomy, regime-routed ensemble forecasting, fill-rate-constrained cost minimization, monthly Business Value Report. Priced per active `PN × Location` under management, discounted for shadow mode, surcharged for DMS/CDC ingestion. Commercial SKU: "Trax IO Core."

### v2 — Causal Demand Forecasting

Adds the Causal Demand Forecaster specialist. Consumes forward flight plans from OCC/commercial scheduling (new ingestion path; not in the 12 v1 SQLs), fleet composition changes, and `eo_published` events. Emits forward-looking demand distributions that replace v1's historical-projection baseline feeding the Policy Engine. Unlocks materially better ROP for parts tied to flying programs. Federated peer-benchmark feature lights up as a premium SKU. Commercial SKU: "Trax IO Causal."

### v3 — AOG & Shortage Risk Agent

Adds the AOG Risk specialist. Scans open WO/EO events, current stock, open orders, vendor performance, and forecasts to predict shortages N days forward, scores AOG risk per tail, and recommends expedites, transfers, interchangeable substitutions, or vendor switches. Tier A only in v3. Hardest sub-problem: the AOG cost model per tenant, shipped with a per-tenant constant and a consulting calibration engagement; automated calibration is a v3.5 research item. Shifts the commercial conversation from "dollars saved in inventory" to "AOG hours prevented." Commercial SKU: "Trax IO AOG Shield."

### v4 — Excess, Obsolete, and Redistribution

Adds the Excess & Redistribution specialist. Identifies slow-movers, idle rotable pool inflation, dead stock at outstations, shelf-life-expiring inventory. Recommends redistribution between stations, return to vendor or core exchange, phase-out, and third-party sale (using `CUSTOMER_ORDER_*` demand signal deferred from v1). First phase to enable AgentCore Code Interpreter for planner-driven scenario math. Commercial SKU: "Trax IO Recovery," with a variable component tied to realized excess reduction.

### v5 — Repair-vs-Buy / Sourcing Optimizer

Adds the Sourcing specialist. For each demand event (actual or forecast), recommends the optimal route: new PO, repair RO, interchange, rental, loan, pool-exchange, cannibalization. Uses `PN_VENDOR_PRICE` across price/condition/lead-time, wash rate, repair cost, criticality, open orders, and v3 AOG urgency. Depends on v3 and v2. Commercial SKU: "Trax IO Sourcing."

### v6 — Rotable Pool Sizing (Multi-Echelon METRIC)

Adds the Rotable Pool specialist. Full multi-echelon optimization across main and outstation hierarchy using METRIC/VARI-METRIC with realistic TAT distributions, interchangeability groups, cannibalization policy, and fleet plan. Requires a proper discrete-event simulator (SageMaker + custom container). The phase that makes Trax uncatchable by SAP/Ramco/IFS in the IM category. Commercial SKU: "Trax IO Network," premium tier, multi-year contract anchor.

### Cross-phase platform investments

Essentiality Mapping service (v1 onboarding deliverable). eMRO Outbound Event Publisher (ships with v1). Repeatable tenant onboarding runbook (ships with v1). SOC 2 Type II audit program (first attestation before lighthouse exits shadow mode). ML ops platform — champion/challenger, red-team suite, model registry, drift detection (foundation in v1). Federated peer-benchmark layer (infrastructure in v1, product-surfaced in v2).

### Explicit non-goals through v6

Trax IO is not a replacement for eMRO's planning or procurement UIs — it recommends; eMRO records and executes. It is not a general-purpose MRO chatbot — scoped to inventory decisions. It is not a demand-planning tool for commercial scheduling — it consumes forward flight plans, it does not generate them. It is not a replacement for the airline's ERP — the write surface is `PN_INVENTORY_LEVEL` only. It is not open-source — the regime-routing heuristics, federated feature layer, and calibrated policy engine constitute the Trax moat.

---

## 9. Risks and open questions

**Forward flight plan ingestion (phase 2).** Not covered by the 12 v1 SQLs. Source system (OCC, commercial ops, IFS/Sabre/Amadeus) and integration pattern must be scoped per tenant before v2 can ship. Flag for pre-v2 design work.

**AOG cost model (phase 3).** Per-tenant calibration is a consulting engagement until v3.5 automation lands. The quality of this model directly determines phase-3 recommendations.

**Prompt injection via free-text eMRO fields.** `REMOVAL_REASON`, `DemandNote`, and similar free-text surfaces are written by mechanics and are an unsanitized injection surface. Defensive prompt construction and strict tool-use scopes already mitigate this, but v1 red-team suite explicitly includes injection payloads and the Guardrail Agent runs output-safety checks before any writeback.

**Tenant Oracle DBA cooperation.** The nightly extract path assumes the customer DBA will run a signed Trax utility. Some customers will require security review. Onboarding runbook includes a pre-approved extract utility review package.

**Bedrock cost ceiling.** Multi-tenant SaaS on a reasoning-heavy agent stack can surprise the P&L. Per-tenant cost observability (§7.7) is the leading indicator; tenant commercial tiers must include either a cost-pass-through or a usage cap with overage pricing.

**Model drift in long-tail parts.** `ultra_rare` regime covers 60–75% of catalog by count. Empirical-Bayes priors rely on peer-part similarity; if the similarity signal breaks (e.g., new fleet introduction with no peer data), forecasts degrade. Phase-2 causal forecasting is the primary mitigation; v1 falls back to the tenant's static `PN_INVENTORY_LEVEL` values when forecast confidence is below a threshold.

---

## 10. Success criteria for v1

Lighthouse tenant in shadow mode within two quarters of plan approval. Ninety days of shadow-mode telemetry showing agent recommendations at least as good as planner decisions on weighted MAPE and cost. First Tier B/C writes on tier-4 expendables within one month of shadow exit. First monthly Business Value Report delivered within 30 days of first production writes. SOC 2 Type II first attestation complete before the lighthouse exits shadow. Second tenant signed before v1.1 scope locks.

---

## Appendix A — Glossary

**AOG** — Aircraft On Ground. Revenue-impacting downtime, typically costed at $10K–$150K per hour depending on fleet and route.
**EOQ** — Economic Order Quantity.
**LRU** — Line Replaceable Unit. A rotable assembly.
**METRIC** — Multi-Echelon Technique for Recoverable Item Control. Canonical multi-echelon rotable optimization framework (RAND/USAF origin).
**PN** — Part Number.
**ROP** — Re-Order Point.
**SS** — Safety Stock.
**TAT** — Turnaround Time (for a rotable in repair).
**TSB / SBA / Croston** — classical intermittent-demand forecasting methods.

## Appendix B — Referenced eMRO objects

`AC_ACTUAL_FLIGHTS`, `AC_MASTER`, `AC_PN_TRANSACTION_HISTORY`, `CUSTOMER_ORDER_DETAIL`, `CUSTOMER_ORDER_HEADER`, `DEFECT_REPORT`, `LOCATION_MASTER`, `NOTE_PAD`, `ORDER_DETAIL`, `ORDER_HEADER`, `ORDER_INVOICE`, `PLANNING`, `PN_EFFECTIVITY_DISTRIBUTION`, `PN_EFFECTIVITY_HEADER`, `PN_INTERCHANGEABLE`, `pn_interchg_one_way`, `PN_INVENTORY_DETAIL`, `PN_INVENTORY_HISTORY`, `PN_INVENTORY_LEVEL`, `PN_MASTER`, `PN_NEXT_LOWER_ASSEMBLY`, `PN_VENDOR_PRICE`, `RELATION_MASTER`, `REQUISITION_DETAIL`, `REQUISITION_HEADER`, `SYSTEM_TRAN_CODE`, `WO`, `WO_ENGINEERING_ORDER`.
