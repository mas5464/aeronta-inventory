# Sub-plan #10 — Tenant Onboarding Runbook

**Goal:** Codify the repeatable process for bringing a new customer tenant from contract signature to first production Tier B/C write, with measurable calibration gates at every stage. Turn what would otherwise be a bespoke consulting engagement into a scripted, auditable, staffable workflow.

**Owner:** Customer success lead (process) + ML engineering (calibration) + SecOps (onboarding security checks) + SRE (provisioning).

**Format:** Operational runbook — not a TDD plan. Every phase has checkpoints, exit criteria, and a named owner.

---

## The onboarding lifecycle

```
Sign  ─►  Provision  ─►  Extract  ─►  Backfill  ─►  Calibrate  ─►
Shadow mode (30-90d)  ─►  Canary (30d)  ─►  Tier C expansion  ─►
Tier B expansion  ─►  BAU (renewal ready)
```

Target: **Sign → first Tier B/C write in 20 weeks.** Faster for customers with clean data and willing DBAs; slower for complex tier-1 carriers.

---

## Phase 1: Security + infrastructure provisioning (1-2 weeks)

**Owner:** SecOps + Platform SRE.

**Artifacts required:**
- Signed Master Service Agreement including the de-identified federated learning opt-in clause.
- SecOps questionnaire completed (customer's InfoSec review package).
- List of customer's eMRO instance endpoints, DBA contacts, IT change-management process.
- Customer's preferred region + data-residency requirements.

**Steps:**
1. Run `trax-io-provision new-tenant <tenant-id>` — Terraform provisions KMS CMK, S3 landing + lake prefixes, DynamoDB online table, Kinesis stream, Glue database, AgentCore Memory namespace, CloudTrail Lake scope.
2. Mint per-tenant mTLS client certificate (for sub-plan #1 upload + sub-plan #3 event publisher). Certificate issued by Trax private CA; expires in 13 months.
3. Mint bearer-token issuer config (sub-plan #6 Writeback auth).
4. Register tenant in the Trax IO Ops dashboard and Audit Manager evidence scope.
5. Add tenant to SOC 2 in-scope list if they're joining during an active audit window.

**Exit criteria:** Tenant appears in Ops dashboard with green health check; empty Iceberg tables created; zero data flowing yet.

---

## Phase 2: Customer DBA briefing + Extract Utility install (1 week)

**Owner:** Customer success + customer DBA.

**Steps:**
1. Deliver security review package for the Extract Utility (sub-plan #1): signed binary, SBOM, network-flow diagram, audit-log schema.
2. Schedule 90-minute briefing with customer DBA: install, configuration, scheduling, smoke run.
3. Customer DBA installs utility, edits `/etc/trax-io/config.toml`, runs `trax-io-extract validate` (no data uploaded, just config + DB connectivity check).
4. Customer DBA runs `trax-io-extract extract --dry-run --date <yesterday>` — produces Parquet locally for DBA review.
5. After DBA sign-off: customer DBA runs first real upload. SRE watches the S3 landing bucket + Glue job.

**Exit criteria:** Three consecutive nightly extracts upload without quarantine. ~400 MB to several GB depending on catalog size.

---

## Phase 3: Historical backfill (1 week)

**Owner:** Data platform SRE.

**Steps:**
1. Run `trax-io-backfill start --tenant <id> --from <YYYY-MM-DD> --to <yesterday>` — parallelized Glue job ingests 24 months of historical extracts.
2. Validate counts match customer's expectations (run a custom reconciliation query per 12 v1 SQLs vs DBA's known totals).
3. Compute derived feature tables (wash rate, lead-time distribution, demand history).

**Exit criteria:** Derived feature tables populated for 24 months; sample spot-check queries from the customer's planning team return sensible numbers.

---

## Phase 4: Essentiality mapping + calibration interview (2 weeks)

**Owner:** ML engineering + customer planning lead.

This is where tribal knowledge becomes configuration. Critical; skip it and every downstream decision is wrong.

**Artifacts produced:**
- `EssentialityMapping` configured per sub-plan #4 Task 5 semantics.
- Initial service-level targets per canonical tier (defaults per design §5.5, overridable).
- Initial autonomy bands (defaults per §6.1, overridable).
- AOG cost model constant (per hour, per fleet — conservative default or customer-provided).
- Default `ordering_cost`, `holding_cost_rate`, `expedite_premium` — required by sub-plan #5 Policy Engine.

**Steps:**
1. Export the customer's `SYSTEM_TRAN_CODE` essentiality codes + descriptions. Auto-propose mapping to canonical 5 tiers using LLM + code descriptions.
2. Customer planning lead reviews and corrects mapping. Auto-propose → human-validated.
3. Interview customer planners on current service-level expectations per tier. Calibrate defaults.
4. Interview customer finance/ops on holding + ordering + expedite cost factors. Document methodology.
5. Lock configuration in the `tenant_config` Iceberg table; changes from here require a ticket.

**Exit criteria:** Signed calibration memo from customer planning lead.

---

## Phase 5: Shadow mode (30-90 days)

**Owner:** ML engineering + customer planning lead.

Shadow mode is the product's single most important risk-mitigation mechanism. During shadow mode, Trax IO produces recommendations for every PN × Location, the Planner UI shows them alongside what the customer's current `PN_INVENTORY_LEVEL` values are, but **nothing writes**. Cedar policy forces Tier A for every part regardless of criticality/cost/delta.

**Steps:**
1. Activate the tenant in the Supervisor orchestrator; nightly recompute begins.
2. Customer planning lead gets daily Planner UI access; reviews 5-10 random recommendations per day, rates each: "would approve" / "would reject" / "would modify".
3. Evaluation pipeline scores every recommendation against the customer's actual `PN_INVENTORY_LEVEL` + their subsequent manual changes.
4. Weekly review: ML engineer + customer planner review regime-classification accuracy, forecast confidence, and notable edge cases.
5. Monthly: first BVR generated (with a "shadow mode — no writes yet" caveat prominent).

**Exit criteria for leaving shadow:**
- Weighted MAPE ≤ tenant target (negotiated in Phase 4).
- Planner "would approve" rate ≥ 70% on reviewed recommendations.
- Zero tenant-isolation incidents.
- Customer planning lead signs off.

Typical duration: 60 days for a lighthouse customer, 30 days for later customers once the pattern is established.

---

## Phase 6: Canary — Tier C narrow (30 days)

**Owner:** Customer success + ML engineering.

Cedar policy is relaxed to enable Tier C (autonomous with digest) for a *very* narrow slice: essentiality-5 consumables under $100, delta within ±20%, single station. Usually ~50-200 PN × Location decisions per month.

**Steps:**
1. Update tenant's Cedar policy with the narrow Tier C carve-out.
2. Planner UI's weekly digest tab populates with the first auto-applied writes.
3. Customer planner reviews digest weekly; any "this was wrong" flag triggers Trax-side investigation.
4. Rollback rate should be < 2%; if higher, narrow the carve-out further or return to shadow.

**Exit criteria:** 30 days with rollback rate < 2% + zero AOG incidents attributable to Trax IO writes.

---

## Phase 7: Tier C expansion (4 weeks)

**Owner:** Customer success.

Progressively widen Tier C per the design §6.1 defaults. Typical expansion schedule:

| Week | Widening |
|---|---|
| 1 | Essentiality 4–5, unit cost < $500, delta ±40%, all stations |
| 2 | Add essentiality 3 (dispatch-critical rotable), delta ±25% |
| 3 | Raise unit-cost cap to $2000 for essentiality 4–5 |
| 4 | Full §6.1 Tier C defaults |

Each weekly step requires clean metrics from the prior week.

---

## Phase 8: Tier B expansion (2 weeks)

**Owner:** Customer success.

Enable Tier B (bounded autonomy with planner notification) per §6.1 defaults. Planner notification traffic increases noticeably; customer planner team trained on how to use the 14-day visible-notification window to catch edge-case writes.

---

## Phase 9: BAU + first renewal conversation (ongoing)

**Owner:** Customer success lead + sales.

Tenant is fully onboarded. Monthly BVR delivered to customer CFO. Quarterly business review with planning lead. First renewal conversation 60 days before contract term end.

---

## Calibration checkpoints

Every phase has measurable criteria that must green-light before advancing. A calibration miss rolls back to the prior phase rather than forcing forward — "we will not break the customer for schedule reasons" is a hard rule.

| Checkpoint | Metric | Threshold |
|---|---|---|
| Exit Phase 4 | Signed calibration memo | Signed |
| Exit Phase 5 | Weighted MAPE | ≤ tenant target |
| Exit Phase 5 | "Would approve" rate | ≥ 70% |
| Exit Phase 6 | Tier C rollback rate | < 2% |
| Exit Phase 6 | AOG incidents attributable to Trax IO | 0 |
| Exit Phase 7 | Each weekly widening gate | Clean metrics from prior week |
| Exit Phase 8 | Tier B override rate | < 10% |

---

## Deliverables

- `trax-io-onboard` CLI that automates Phase 1 + Phase 3 + status dashboard.
- Customer-facing onboarding guide (PDF + HTML).
- Calibration interview script for ML engineer.
- Email templates for each phase's stakeholder communication.
- Metrics dashboard showing onboarding phase + days in phase for every tenant.
- Runbook for phase rollbacks ("pilot isn't working — how do we go back to shadow").

## Estimated timeline (lighthouse customer)

- Phases 1-4: 5-6 weeks elapsed (parallelized where possible).
- Phase 5 (shadow): 60 days.
- Phase 6 (canary): 30 days.
- Phases 7-8 (expansion): 6 weeks.
- Total: ~20 weeks to first real Tier B/C writes at scale.

## Team

1 customer success lead (dedicated per tenant for Phases 1-7) + 0.5 ML engineer (shared across tenants; dedicated during Phase 4 + Phase 5 first 2 weeks) + 0.25 SRE (shared) + 0.25 SecOps (shared).
