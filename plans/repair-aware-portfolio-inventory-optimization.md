# Plan: Repair-Aware Portfolio Inventory Optimization

> Source PRD: Conversation-approved “Repair-Aware Portfolio Inventory Optimization” PRD (2026-07-28)

**Status:** All 12 engineering phases complete on 2026-07-28. The advisory
tenant feature flag remains default-off pending pilot-specific coverage review,
trusted replay-package import, and named operational ownership.

## Architectural decisions

Durable decisions that apply across all phases:

- **Delivery order**: Baseline correctness is the first release gate, repair-supply modeling is the second, and portfolio optimization is the third. Shadow validation and the production-scale advisory gate follow. A later phase cannot weaken an invariant established by an earlier phase.
- **Browser routes**: Add `/portfolio` for portfolio planning. Extend the existing `/parts/:pn/:location` drill-down with repair and candidate evidence, `/data` with repair-history intake and coverage, and `/scenarios` with independent procurement-lead and repair-TAT controls.
- **API routes**: Keep the BFF as the only browser-facing API under `/v1/tenants/{tenant_id}`. Add the resource-oriented `/planning-runs` collection, `/planning-runs/{run_id}` detail, and `/planning-runs/{run_id}/selections` detail contract. Extend the existing part-context, scenario, upload, ingest, and feed-health contracts additively.
- **Compute boundary**: Interactive single-key previews and scenarios may remain synchronous. Full portfolio planning and historical replay run asynchronously through the existing Postgres job queue and Railway worker. The recommendation engine and feature store are never exposed directly to the browser.
- **Supply-cycle schema**: Reuse the existing supply-cycle distribution keyed by tenant, part, vendor, and condition. `NEW` represents procurement lead time and `REP` represents repair cycle time. Source, grouping level, observation count, model version, confidence, data cutoff, and proxy definition travel with the distribution.
- **Repair input models**: Normalize completed repair history as `RepairCycleObservation` and open work as `RepairWorkItem`. Stable repair-order and line identity are required; serial identity is optional. Missing identity or lifecycle age produces conservative exclusion rather than inferred supply.
- **Repair output model**: `RepairReturnProfile` represents horizon-specific expected units, variance, probability bands, eligible work, exclusions, and evidence. Descriptive repair TAT and projected repair receipts remain separate concepts.
- **Planning models**: `PolicyCandidate` is the versioned per-key option contract. `PlanningRun` is the immutable portfolio snapshot, configuration, status, solver evidence, summary, and lineage contract. `PortfolioSelection` records the current, selected, and nearest rejected alternatives for one inventory key.
- **Persistence**: Postgres stores tenant-scoped planning-run headers, summaries, selections, and job state using the existing JSONB-source-of-truth plus query-scalar pattern. Raw repair history remains in uploaded/source artifacts and the feature layer rather than becoming a second repair-history warehouse in Postgres.
- **Deep module boundaries**: The Repair Return Model produces probabilistic supply; the Item Candidate Planner produces feasible per-key candidate frontiers without knowing the portfolio budget; the Portfolio Optimizer selects among candidates without reading source data or forecasting; and the Planning Run facade coordinates immutable inputs, persistence, and results.
- **Optimization model**: Use a multiple-choice knapsack or equivalent mixed-integer model through the existing in-process SciPy/HiGHS boundary. Select exactly one candidate per eligible key while enforcing a hard incremental acquisition-cash budget and mandatory floors. No hosted optimization service is introduced.
- **Objective**: Maximize tenant-criticality-weighted expected shortage and AOG-risk reduction, less configured holding and ordering cost penalties. One run uses one planning horizon and one tenant base currency.
- **Solver semantics**: Prefer a proven optimal result. If the configured time limit ends first, return the feasible incumbent, bound, gap, and an explicit `not_proven` state. Tie-break equal objectives by lower spend and then stable key/candidate order.
- **Identity and idempotency**: A planning fingerprint includes the immutable source snapshot, tenant policy, observation window, forecast version, repair-model version, candidate-planner version, optimizer version, objective definition, budget, horizon, and currency. Identical fingerprints reuse the same logical run; any material change creates a different run.
- **Ingestion parity**: Read-only Oracle/eMRO extraction and self-serve CSV/Excel upload normalize into the same order, repair, and feature contracts. The upload surface gains one optional repair-history feed rather than a separate repair subsystem.
- **Authorization**: Supabase JWT claims, canonical tenant resolution, tenant-scoped BFF paths, explicit feature-store tenant context, and Postgres RLS remain the isolation model. A planner-or-higher role may start planning runs; authorized tenant members may read advisory results. Elevated worker access remains tenant-bound and never reaches the browser.
- **External services**: Supabase remains the Auth, Postgres, and upload-Storage boundary. Railway remains the BFF and worker runtime. The existing AWS S3/Glue/Iceberg/DynamoDB path remains the offline and online feature boundary for native connector deployments. No new third-party service is required.
- **Contract evolution**: New response fields are additive and default-safe. Saved scenarios and planning-run payloads carry an explicit contract version before field meaning changes.
- **Advisory boundary**: Every result remains shadow/advisory. No phase in this plan may create a purchase order, transfer, repair routing decision, or min/max writeback. Existing audited writeback boundaries remain unchanged.
- **Testing philosophy**: Test external contracts and business invariants, not private functions, solver variable layout, or incidental class structure. Preserve existing contract-equivalence, property, tenant-isolation, ingestion, scenario, idempotency, and end-to-end patterns.

---

## Phase 1: Time-Correct Key Economics

**User stories**: 25–30

**Contract**: [Planning Calculation Trace Contract](../docs/contracts/2026-07-28-planning-calculation-trace-contract.md)

### What to build

Deliver a complete planning trace for one part-location key from either native extract or canonical upload through the recommendation API and part drill-down. Correct demand exposure, zero-demand periods, event-versus-unit semantics, scheduled demand, open receipts, and constraint evidence so the user can see exactly which quantities entered the selected planning horizon.

### Acceptance criteria

- [x] Demand exposure uses the configured observation start and end rather than a fixed divisor unrelated to the available history.
- [x] Demand-event count and demanded-unit quantity remain distinct through classification, projection, API evidence, and UI labels.
- [x] Leading and trailing zero-demand periods are represented consistently and do not disappear when the key is inactive.
- [x] Scheduled demand and open receipts enter only the horizons for which their due dates qualify under one documented boundary convention.
- [x] Every applied operational constraint appears in the planning trace with its source and binding state.
- [x] Tenant-scoped part context exposes the demand, supply, horizon, and constraint trace without exposing the engine directly.
- [x] The part drill-down renders the trace and reconciles its displayed inputs to the underlying recommendation.
- [x] Boundary-date, zero-demand, scheduled-demand, open-receipt, and constraint behavior is covered by contract and property tests.
- [x] Existing recommendation, scenario, and tenant-isolation regression suites remain green.

---

## Phase 2: Versioned Per-Key Candidate Frontier

**User stories**: 31–39

### What to build

Turn the corrected one-key calculation into a versioned candidate preview. The planner can compare the current policy, a no-change option, and a compact frontier of feasible alternatives with reconciled action quantities, economics, service or shortage effects, model identity, constraints, and evidence.

### Acceptance criteria

- [x] Final quantities, costs, and benefits are recomputed after constraints and arbitration and reconcile exactly in the response.
- [x] The served forecast and policy model names match the models that actually generated every candidate.
- [x] The candidate fingerprint includes all data, configuration, model, and objective versions that can change the result.
- [x] Repeating an identical preview produces the same fingerprint and observationally equivalent frontier.
- [x] Changing any fingerprint component produces a distinct identity and prevents false deduplication.
- [x] Every eligible key receives a no-change candidate when no-change satisfies its hard constraints.
- [x] Every candidate exposes target levels, proposed action, quantity, spend, expected service or shortage, lifecycle-cost components, confidence, binding constraints, and evidence.
- [x] Dominated candidates are removed only when their removal cannot alter any feasible portfolio optimum.
- [x] The part drill-down presents current versus candidate choices, truthful model labels, the fingerprint, and the number of dominated options removed.
- [x] Candidate feasibility, no-change behavior, dominance pruning, model labeling, economics reconciliation, determinism, and fingerprint sensitivity have black-box tests.

---

## Phase 3: Purchase vs. Repair Lane Separation

**User stories**: 1–6

### What to build

Carry one purchase order and one repair order for the same part through extraction, feature materialization, part context, and the UI as two visibly independent supply-cycle lanes. Users see a `NEW` procurement distribution and a `REP` repair-TAT distribution with their own statistics, sources, confidence, and sample coverage.

### Acceptance criteria

- [x] Closed supply records retain an explicit purchase-versus-repair order classification at the ingestion boundary.
- [x] Purchase records contribute only to `NEW`; repair records contribute only to `REP`.
- [x] Each distribution exposes mean, p50, p90, p99, observation count, source, grouping level, confidence, data cutoff, and model version.
- [x] Creation-to-receipt repair cycles are labeled “RO cycle-time proxy” everywhere until physical induction and serviceable-completion events are available.
- [x] Missing `NEW` or `REP` evidence produces a typed unavailable or fallback state rather than a blended generic lead time.
- [x] Tenant-scoped part context returns both lanes independently and preserves their provenance.
- [x] The part drill-down renders separate procurement-lead and repair-TAT cards with unambiguous labels.
- [x] Tests prove that PO rows cannot change `REP`, RO rows cannot change `NEW`, and missing-lane behavior fails conservatively.
- [x] Native feature-store and in-memory contract implementations remain observationally equivalent for both conditions.

---

## Phase 4: Repair-History Intake and Coverage

**User stories**: 7–12

### What to build

Add an optional self-serve repair-history input to the existing direct-upload and asynchronous-ingest flow. A planner can submit repair lifecycle data, receive actionable row-level validation, preserve the prior tenant snapshot on failure, and see observed, pooled, proxy, and unavailable coverage after a successful ingest.

### Acceptance criteria

- [x] The public canonical contract accepts repair order identity, part, vendor or shop, quantity, lifecycle timestamps, status, location, outcome when available, and optional serial identity.
- [x] CSV and Excel representations normalize to the same `RepairCycleObservation` contract as native connector data.
- [x] Missing identity, invalid type, impossible quantity, end-before-start duration, duplicate terminal event, and contradictory status produce structured validation errors.
- [x] Validation is all-or-nothing for the tenant snapshot; a failed repair-history ingest does not partially replace previously valid planning data.
- [x] Sparse evidence follows the approved grouping hierarchy and never substitutes procurement lead for repair TAT.
- [x] Configured repair promises may appear as low-confidence context but are never labeled as observed history.
- [x] Ingest results report accepted, excluded, and quarantined counts plus repair-history coverage by part and shop.
- [x] Data & Connections shows repair-feed status, validation outcomes, coverage, fallback use, and the proxy definition.
- [x] Role rules match existing ingest behavior: planner-or-higher can upload; authorized viewers can inspect status and coverage.
- [x] Native-extract and self-serve-upload fixtures produce equivalent repair feature contracts.
- [x] Upload, validation, atomicity, coverage, role, and UI-state tests cover valid, malformed, duplicate, sparse, and empty inputs.

---

## Phase 5: Open-Repair Identity and Conservative Supply

**User stories**: 13–18

### What to build

Represent open repair work as identifiable units or order lines and show which quantity is eligible for future-return modeling. Resolve overlap between open repair orders and aggregate in-repair balances before any repair credit reaches the inventory position.

### Acceptance criteria

- [x] Every eligible open repair carries tenant, repair-order and line identity, part, quantity, location, opened age, status, and shop when available.
- [x] Serial identity is preserved when supplied but is not required for a non-serialized order-line quantity.
- [x] Identifiable open repair orders are the canonical source for time-phased repair supply.
- [x] Aggregate in-repair quantity contributes only a positive residual after identifiable open repair quantities are removed.
- [x] Ambiguous, unlinked, missing-age, terminal, or otherwise ineligible units receive zero time-phased repair credit and an exclusion reason.
- [x] Eligible repair quantity can never exceed physical open repair WIP.
- [x] The part-context API exposes included and excluded repair positions with evidence and warning codes.
- [x] The part drill-down shows an open-repair pipeline with included, excluded, and residual quantities and any data-quality warning.
- [x] Tests cover duplicate order lines, duplicate serials, overlap with aggregate WIP, overlap with open receipts, partial quantities, missing identity, and conservative exclusion.
- [x] Portfolio totals and part-level repair quantities conserve units across every tested path.

---

## Phase 6: Age-Conditioned Returns and Independent Scenarios

**User stories**: 19–24

### What to build

Estimate horizon-specific repair returns from completed and right-censored repair history and the current age of eligible open work. Expose expected units and probability bands, then let users change procurement lead and repair TAT independently in the scenario experience.

### Acceptance criteria

- [x] Completed repair cycles contribute observed durations and eligible open cycles contribute right-censored observations.
- [x] Each open item’s residual return probability is conditioned on its current age rather than restarting its repair clock.
- [x] Return probabilities stay within zero and one and are nondecreasing as the planning horizon grows.
- [x] Expected repair receipts stay between zero and eligible open WIP and include any validated serviceable-yield assumption in their evidence.
- [x] Slower repair TAT weakly decreases expected returns within a fixed horizon.
- [x] Repair returns affect only eligible repairable or rotable items.
- [x] Procurement-lead changes cannot alter repair-TAT estimates, and repair-TAT changes cannot alter procurement-lead estimates.
- [x] The part-context API exposes `RepairReturnProfile` statistics, exclusions, confidence, and provenance.
- [x] The part drill-down visualizes return probabilities and expected units without false precision.
- [x] The scenario contract and UI expose separate procurement-lead and repair-TAT controls with additive defaults for previously saved scenarios.
- [x] Saved scenario results identify which assumption changed, the affected keys, and a versioned fingerprint.
- [x] Survival, censoring, residual-life, bounds, monotonicity, eligibility, lane independence, saved-scenario compatibility, and UI wiring have black-box tests.

---

## Phase 7: First Hard-Budget Portfolio Solve

**User stories**: 40–46

### What to build

Deliver the first bounded end-to-end portfolio run for a small explicit key scope. A planner enters a base-currency cash budget, the worker selects one candidate per key, and the `/portfolio` workspace displays selections, no-change choices, spend, slack, mandatory floors, or an explicit infeasible result.

### Acceptance criteria

- [x] A planner-or-higher user can submit a tenant-scoped run with a snapshot, explicit key scope, budget, horizon, currency, and model profile.
- [x] Submission creates an immutable planning-run identity and a queued worker job in one consistent operation.
- [x] The budget means incremental acquisition cash committed within the run horizon and is applied consistently to every candidate.
- [x] The optimizer selects exactly one candidate per eligible key.
- [x] Total selected acquisition cash never exceeds the hard budget.
- [x] Mandatory safety and criticality floors remain hard and are never relaxed silently.
- [x] A feasible no-change option may be selected and remains visible in the result.
- [x] An impossible budget returns an explicit infeasible state, affected floors, and best-known minimum shortfall.
- [x] The `/portfolio` workspace renders the budget meter, spend, slack, per-key choices, floor state, and infeasibility guidance.
- [x] Selected detail rows reconcile exactly to portfolio spend and key counts.
- [x] Small instances match a brute-force oracle and property tests cover budget, one-per-key, floors, zero-budget, ample-budget, infeasibility, and arithmetic reconciliation.
- [x] Two-tenant route, job, run, and selection tests prove claim, BFF, and RLS isolation.

---

## Phase 8: Tenant-Weighted Deterministic Optimizer

**User stories**: 47–53

### What to build

Add the complete approved objective and reproducibility contract. A tenant can configure criticality and cost weights, see how each selected candidate contributes to the objective, and distinguish a proven optimum from a feasible result whose optimality is not yet proven.

### Acceptance criteria

- [x] The objective maximizes criticality-weighted expected shortage and AOG-risk reduction less configured holding and ordering cost penalties.
- [x] Tenant objective weights and mandatory floors are versioned planning inputs with safe validated defaults.
- [x] Changing a tenant weight can change selection while leaving unrelated tenant configuration untouched.
- [x] Identical inputs produce the same selections, totals, objective, and stable tie-break ordering.
- [x] Ties resolve by higher objective, then lower spend, then stable inventory-key and candidate order.
- [x] Increasing a feasible budget cannot worsen the optimized objective.
- [x] Ample budget reproduces the independently preferred feasible candidate for every key.
- [x] The result reports solver implementation and version, termination state, objective, bound, gap, duration, and whether optimality was proven.
- [x] A time-limited feasible incumbent is labeled `not_proven`; it is never silently presented as exact.
- [x] The `/portfolio` workspace shows objective components, tenant weights, and an exact-versus-gap status with plain-language guidance.
- [x] Small exact fixtures, randomized candidate menus, input permutations, zero and ample budgets, weight changes, monotonicity, tie-breaks, and bounded-gap behavior have automated tests.

---

## Phase 9: Asynchronous Full-Portfolio Run Lifecycle

**User stories**: 54–59

### What to build

Expand the bounded solver slice into the durable full-portfolio workflow. Users can submit, leave, return, and inspect queued, running, completed, infeasible, failed, or stale runs while the worker processes one immutable tenant snapshot and records skipped keys and warnings safely.

### Acceptance criteria

- [x] `/planning-runs` supports tenant-scoped submission and recent-run listing; run detail exposes status, progress, timestamps, summary, warnings, and terminal evidence.
- [x] A run uses one immutable source snapshot even if newer tenant data arrives while it is executing.
- [x] A completed run remains reproducible and receives a visible stale-data badge when a newer snapshot exists.
- [x] Identical submissions resolve idempotently to the same logical run without duplicate worker execution.
- [x] The worker persists a claimed state before long-running computation and reaches a safe terminal state after success, infeasibility, retry exhaustion, or controlled failure.
- [x] Skipped keys and partial-coverage warnings carry stable reason codes and counts; they are never hidden in logs only.
- [x] Missing model artifacts, invalid candidate menus, solver errors, and persistence failures cannot produce an actionable-looking completed plan.
- [x] The `/portfolio` workspace provides run history, polling, progress, terminal states, warning and skip summaries, and stale-snapshot guidance.
- [x] Authorized viewers may inspect results, while only planner-or-higher users may start a run.
- [x] Lifecycle, retry, recovery, idempotency, immutable snapshot, staleness, skipped-key, authorization, safe-failure, and UI polling states have integration tests.

---

## Phase 10: Explain, Reconcile, and Rerun

**User stories**: 60–64

### What to build

Make a completed plan operationally explainable. Users can drill from portfolio totals to one key, compare current, selected, and rejected alternatives, understand the budget or constraint tradeoff, and rerun a saved configuration with explicit parent-child lineage and a new fingerprint when an assumption changes.

### Acceptance criteria

- [x] Each selection records the current policy, selected candidate, and nearest meaningful rejected alternatives with their spend, service, objective, confidence, and constraints.
- [x] Every selected candidate has a plain-language reason tied to objective contribution, budget, mandatory floors, and evidence.
- [x] Rejected alternatives identify the binding reason without claiming that budget was decisive when another constraint caused rejection.
- [x] Portfolio spend, objective, service or shortage, key counts, warnings, and confidence totals reconcile exactly to selection details.
- [x] Run detail supports stable pagination and filtering without changing aggregate totals.
- [x] The `/portfolio` workspace supports portfolio-to-key drill-down and current-versus-selected-versus-rejected comparison.
- [x] A saved run configuration can be rerun with a different budget or repair assumption while preserving the parent run and assumption diff.
- [x] An unchanged rerun reuses the logical fingerprint; a material change creates a new fingerprint and run identity.
- [x] Explanations and lineage remain immutable after a run reaches a terminal state.
- [x] Explanation completeness, rejection correctness, pagination, total reconciliation, parent-child lineage, fingerprint diff, and UI drill-down have black-box tests.

---

## Phase 11: No-Lookahead Replay and Shadow Governance

**User stories**: 65–72

### What to build

Evaluate current and challenger decisions using historical `as_of` snapshots and only the information available at each decision date. Publish a tenant-scoped shadow scorecard with matched comparisons, operational outcomes, cohort breakdowns, lineage, and an enforced advisory-only state.

### Acceptance criteria

- [x] Every replay decision has an explicit `as_of` cutoff and excludes later demand, receipts, lifecycle outcomes, prices, model artifacts, and configuration changes.
- [x] Replay reports fill or service, backorders, shortage exposure, inventory investment, holding cost, ordering cost, and the approved AOG-risk proxy.
- [x] Current and challenger policies can be compared at matched budget or matched service using a documented comparison rule.
- [x] Results segment by criticality, demand regime, repairability, location, and repair-data confidence without losing the portfolio aggregate.
- [x] Metric denominators, units, exclusions, and data coverage are visible with the scorecard.
- [x] A shadow-review package links every metric and selected plan to its snapshot, model versions, objective, candidate frontier, and solver evidence.
- [x] The API and `/portfolio` validation view mark all replay and shadow results advisory-only.
- [x] No replay, scenario, or shadow action can call purchase, transfer, repair-routing, or policy-writeback operations.
- [x] Tenant claims, explicit tenant context, RLS, and tenant-bound worker writes protect replay inputs and results.
- [x] No-lookahead fixtures, matched-comparison arithmetic, cohort reconciliation, lineage, tenant isolation, advisory immutability, and UI caveats have automated tests.

---

## Phase 12: Production Contract and Full-Network Launch Gate

**User stories**: 73–80

### What to build

Stabilize the advisory planning contract and prove it at the current full-network workload for a feature-flagged tenant. Expose coverage, uncertainty, operational telemetry, accessible result states, and safe failures so product, engineering, and operations can make an evidence-based shadow-launch decision.

### Acceptance criteria

- [x] The versioned planning-run submission, status, summary, and selection contracts are documented and locked by consumer contract tests.
- [x] API responses consistently expose data coverage, TAT confidence, repair-credit coverage, skipped keys, warnings, solver evidence, and model lineage.
- [x] Operational metrics cover ingestion validation, PO/RO classification, fallback use, repair deduplication, candidate counts, worker duration, solver duration, feasibility, optimality gap, reconciliation, and failures.
- [x] Invalid data, missing artifacts, worker interruption, solver timeout, and persistence failure return structured safe states with sensitive details redacted.
- [x] The `/portfolio`, part-detail, scenario, and Data & Connections experiences present low-confidence and unavailable data without false precision.
- [x] Progress, result, table, dialog, warning, and error experiences are keyboard accessible and carry meaningful accessible names and announcements.
- [x] React Query and client caches remain tenant-scoped during tenant switching and cannot display a prior tenant’s planning data.
- [x] A full-network benchmark uses production-representative candidate counts, repair-data sparsity, and concurrent tenant activity.
- [x] The complete workflow fits within the established planning batch window or returns explicit bounded-gap evidence under the approved time limit; it never silently downgrades to a heuristic.
- [x] A feature flag can enable the advisory workflow for a selected tenant without changing other tenants or granting write authority.
- [x] End-to-end tests cover authorized submission, worker execution, completed retrieval, explanation drill-down, shadow scorecard, export if enabled, and proof that no writeback occurs.
- [x] The launch record captures benchmark results, known coverage limitations, operational owners, rollback procedure, and the explicit decision to remain advisory-only.
