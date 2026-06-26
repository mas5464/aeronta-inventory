# Sub-plan #8 — Business Value Report Pipeline Implementation Plan

**Goal:** Ship the monthly per-tenant Business Value Report (BVR) — an auto-generated, Trax-branded PDF + Planner-UI card that quantifies savings attributable to Trax IO over the prior month and contextualizes them against the customer's baseline. This is the contract-renewal engine. Productized in v1 per the design §7.3.

**Owner:** ML engineering team (attribution methodology) + Trax IO platform (pipeline + rendering) + Design (Trax-branded templates).

**Tech Stack:** Python 3.12, Glue (monthly job), `pandas` + `numpy` for attribution math, `WeasyPrint` for HTML → PDF, Jinja2 templates, Chart.js rendered server-side via `pyppeteer` + headless Chromium for high-quality chart images.

---

## What the report contains

The monthly BVR is the single most-important asset for contract renewal. It answers four questions the customer's planning lead asks their CFO:

1. **"Did Trax IO save us money?"** — Attributed dollar savings this month, running total since onboarding, and variance vs. baseline projection.
2. **"Did Trax IO prevent stockouts?"** — Realized fill rate vs. target per essentiality tier. AOG-critical fill rate is the headline number.
3. **"Is it safe?"** — Override rate, rollback rate, planner trust score trend. Audit-ready evidence that humans remain in the loop.
4. **"What's next?"** — Coming-month projections, known risks, the three highest-value optimization opportunities that didn't make it through autonomy bands and still need planner attention.

### Report sections

1. **Executive summary** (1 page) — hero numbers in large type.
2. **Savings attribution** (2 pages) — methodology-explicit decomposition.
3. **Service level realized** (2 pages) — fill rate per tier + stockout log.
4. **Governance** (1 page) — override rate, rollbacks, audit log summary.
5. **Forward look** (2 pages) — projected next-month savings, top risks, top planner-required decisions.
6. **Appendix** (3–5 pages) — full methodology, model versions used, holdout evaluation metrics, SOC 2 evidence references.

---

## Phases

### Phase 0: Attribution methodology review + customer sign-off (3 weeks)

The methodology defines how we compute "savings attributable to Trax IO." Get this wrong and every number in the report is disputed at renewal time.

- Baseline = counterfactual `PN_INVENTORY_LEVEL` trajectory derived from the tenant's own pre-agent history using a standard holding-cost + ordering-cost + stockout-proxy model.
- Attribution decomposition:
 - **Holding cost delta** = (baseline_inventory_value − actual_inventory_value) × holding_cost_rate.
 - **Ordering cost delta** = (baseline_order_count − actual_order_count) × per_order_cost.
 - **Expedite delta** = (baseline_expedites − actual_expedites) × expedite_premium.
 - **Stockout delta** = (baseline_stockouts − actual_stockouts) × stockout_cost_proxy (conservative — we do NOT monetize AOG prevention in v1; that's v3).
 - **Total attributed** = sum of the above.
- Methodology signed off by lighthouse customer CFO before first report ships.

### Phase 1: Counterfactual baseline generator (3 weeks)

**Files:** `src/trax_io_bvr/baseline.py`, `tests/unit/test_baseline.py`

- Glue job consumes sub-plan #2's Iceberg feature tables.
- Computes, for each PN × Location currently under Trax IO management, what the demand + stock + order trajectory *would have been* if `PN_INVENTORY_LEVEL` had never changed from its pre-onboarding value.
- Runs once per tenant per month (relatively expensive — 2–3 hours Spark for a tier-1 carrier's catalog).
- Output: Iceberg table `counterfactual_baseline_{tenant_id}_{YYYYMM}`.

### Phase 2: Attribution job (2 weeks)

**Files:** `src/trax_io_bvr/attribution.py`

- Joins counterfactual with actual.
- Computes each delta component per `(PN × Location × day)`, sums to tenant level, breaks down by essentiality tier, by ATA chapter, by fleet.
- Output: `monthly_attribution_{tenant_id}_{YYYYMM}` Iceberg table.

### Phase 3: Realized service level calculator (2 weeks)

**Files:** `src/trax_io_bvr/service_level.py`

- Joins historical recommendations with subsequent stockout events (from sub-plan #2's `demand_history` + `stock_amount` time-travel queries).
- Computes realized fill rate per essentiality tier, per month.
- Identifies every stockout and attributes it to either "predicted correctly but planner overrode" vs "model miss" vs "baseline-level would also have stocked out".

### Phase 4: Governance metrics (1 week)

**Files:** `src/trax_io_bvr/governance.py`

- Override rate per regime, per tier.
- Rollback count, with sampled rollback reasons.
- Planner trust score (composite) with 6-month trend chart.
- Kill-switch engagements (ideally zero, any engagement requires annotation).

### Phase 5: Forward projection (2 weeks)

**Files:** `src/trax_io_bvr/projection.py`

- Simple moving-average projection of next-month savings based on trailing 3 months.
- Top-3 risks surfaced from the Supervisor's open-approval queue: highest-priority recommendations still awaiting planner action.
- Honest caveat block: what we don't know and what could change.

### Phase 6: HTML template + Trax branding (3 weeks)

**Files:** `src/trax_io_bvr/templates/`

- Jinja2 templates per section.
- Chart.js specs rendered to PNG via headless Chromium (server-side) and embedded in the HTML.
- Trax brand colors, typography, logo per Trax brand guidelines.
- Print-optimized CSS.

### Phase 7: PDF generator (1 week)

**Files:** `src/trax_io_bvr/renderer.py`

- `WeasyPrint` converts HTML → PDF.
- Embedded fonts for typographic consistency.
- PDF/A-3 compliance for long-term archival (SOC 2 audit trail).

### Phase 8: Monthly scheduler + delivery (2 weeks)

- EventBridge monthly rule triggers the pipeline on day 3 of each month for the prior month's data.
- Output PDF stored in per-tenant S3 with 7-year retention (Object Lock).
- Posted to the Planner UI "Reports" tab.
- Email notification to configured tenant contact list.

### Phase 9: Internal Trax-side dashboard (2 weeks)

Same attribution numbers surface in an internal Trax Ops dashboard showing every tenant's monthly savings, trust score, and risk profile. Sales renewal team consumes this.

### Phase 10: A/B the report design with the lighthouse customer (4 weeks)

Ship two report variants to the lighthouse customer for three months running. Planner + CFO pick preferred layout; iterate.

---

## Acceptance criteria

- Report generates for every active tenant by day 7 of each month.
- Attribution math reproducible: given the same inputs, two runs produce identical output to the dollar.
- Every number in the PDF traceable to a specific feature-store query + model version via provenance cross-reference.
- PDF is PDF/A-3 compliant.
- Report passes accessibility review (tagged PDF, readable by screen readers).

## Deliverables

- Monthly BVR pipeline (Python + Glue + PDF rendering).
- Counterfactual baseline methodology doc signed off by lighthouse customer CFO.
- Sample BVR for lighthouse customer covering first shadow-mode month.
- Internal Trax Ops dashboard.
- Runbook for monthly delivery.

## Risks

- **Counterfactual disputes.** The CFO disagrees with the baseline methodology. Mitigation: Phase 0 sign-off; re-run with their preferred methodology as a sanity check.
- **Attribution volatility** early in the tenant's lifecycle. Mitigation: suppress headline numbers until 90 days of data, show "directional" early.
- **PDF rendering fragility.** Headless-browser chart rendering is notoriously flaky. Mitigation: Chart.js server-side render caching + fall-back to pre-rendered static charts if rendering fails.

## Estimated timeline

~14 weeks elapsed; 1 ML engineer + 1 platform engineer + 0.5 designer.
