# Repair-Aware Portfolio Shadow Launch Record

**Date:** 2026-07-28
**Decision:** Engineering pass for a feature-flagged, advisory-only shadow
pilot. Enabling a tenant remains pending that tenant's coverage/TAT review,
approved replay package, and named operational rotation.
**Not approved:** purchase orders, transfers, repair routing, policy writeback,
or any other operational mutation.

## Decision boundary

This release produces immutable planning runs and historical shadow scorecards.
It does not execute a selected candidate. The planning and replay contracts
contain `advisory_only=true`; their browser routes expose no approve, apply,
commit, purchase, transfer, repair-routing, or writeback operation.

The workflow is default-off. A tenant is enabled only when its canonical slug is
present in the BFF `PLANNING_ENABLED_TENANTS` allowlist. Removing the slug and
redeploying disables new and existing planning/replay browser access for that
tenant without changing any other tenant or granting write authority.

## Full-network benchmark

Command:

```bash
cd services/recommendation-engine
PYTHONPATH=src:../feature-store/src .venv/bin/python \
  benchmarks/portfolio_full_network.py \
  --keys 58899 \
  --tenants 2 \
  --solver-time-limit 300 \
  --batch-window 900
```

The benchmark executes the real immutable request contract, canonical planning
fingerprint, sparse SciPy/HiGHS multiple-choice model, exact-Decimal result
reconciliation, and two concurrent tenant runs. It does not substitute a
heuristic.

| Measure | Result |
|---|---:|
| Concurrent tenants | 2 |
| Keys per tenant | 58,899 |
| Candidates per tenant | 176,696 |
| Mean candidates per key | 3.0 |
| Keys with repair evidence per tenant | 11,779 (approximately 20%) |
| Hard planning budget per tenant | 2,355,960 USD |
| Selected acquisition cash per tenant | 2,355,900 USD |
| Selected keys per tenant | 58,899 |
| Wall time | 359.487 seconds |
| Batch window | 900 seconds |
| Tenant isolation | Passed; distinct fingerprints and exact tenant key counts |
| Solver state | `not_proven` for both tenants |
| Bound/gap evidence | Present for both tenants |
| Overall benchmark gate | Passed |

The primary objective produced feasible, budget-compliant incumbents with
bound/gap evidence, but the complete stable-order tie-break proof did not finish
inside the configured solver limit. The result therefore remains visibly
`not_proven`; it is not relabeled exact. This is an accepted shadow-pilot
limitation, not permission to execute the plan.

## PostgreSQL lifecycle scale gate

Command:

```bash
cd services/agent-spine
PG_PLANNING_FULL_LIFECYCLE_BENCH=1 \
PYTHONPATH=src:../recommendation-engine/src:../feature-store/src:../forecasting/src:../event-publisher/src \
  .venv/bin/python -m pytest \
  tests/pg/test_planning_full_lifecycle_scale.py -q -s
```

This second gate exercises submission, normalized immutable-menu storage,
worker claim and reconstruction, the real optimizer, incremental explanation
derivation, exact reconciliation, normalized selection persistence, and bounded
poll/job headers in PostgreSQL.

| Measure | Result |
|---|---:|
| Keys / normalized menus / persisted selections | 58,899 / 58,899 / 58,899 |
| Candidates | 176,696 |
| Keys with repair evidence | 11,779 |
| Request build | 2.339 seconds |
| Submission | 30.686 seconds |
| Worker solve, reconcile, and persist | 382.522 seconds |
| End-to-end wall time | 415.547 seconds |
| Batch window | 900 seconds |
| Peak process RSS | 4,871,372,800 bytes (4.54 GiB) |
| Request / result / detail / job headers | 863 / 1,077 / 222 / 1,566 bytes |
| Exact database cardinality and bounded-header gates | Passed |
| Overall lifecycle gate | Passed |

The incremental worker path reduced the prior development measurement from
962.210 to 415.547 seconds and peak RSS from 8,819,933,184 to 4,871,372,800
bytes without relaxing the launch threshold.

## Coverage and modeling limitations

- Completed repair cycles and eligible open cycles support the repair-return
  model. Missing identity, lifecycle age, or admissible history receives zero
  time-phased repair credit and a stable exclusion reason.
- The present repair TAT remains a **repair-order creation-to-receipt cycle-time
  proxy**. It is not physical induction-to-serviceable-completion TAT.
- The benchmark deliberately models only approximately 20% repair-evidence
  coverage. Uncovered keys remain visible as unavailable/fallback; they are not
  silently assigned procurement lead time as repair TAT.
- The benchmark uses deterministic synthetic economics and candidate shapes so
  it can run without customer data. A pilot tenant still requires its own
  coverage review before the flag is enabled.
- No AWS data-plane or compute-plane deployment is asserted by this record.
  Current production runtime remains the documented Supabase/Railway/Vercel
  application boundary.

## Operational ownership

| Responsibility | Accountable owner |
|---|---|
| Shadow-pilot go/no-go and tenant approval | VP, Head of Innovation (Miguel Sosa) |
| BFF and worker runtime | Aeronta Inventory service on-call |
| Postgres migrations, RLS, and recovery | Aeronta data-platform owner |
| Native/upload feed quality and repair coverage | Tenant onboarding/data-integration owner |
| Optimizer/model version and benchmark review | Inventory optimization engineering owner |
| Planner review and feedback | Enabled tenant's designated planning lead |

Named rotations and escalation contacts remain deployment configuration; they
must be assigned before enabling a customer tenant.

## Rollback procedure

1. Remove the tenant slug from `PLANNING_ENABLED_TENANTS` and redeploy the BFF.
   Confirm the capability endpoint reports `feature_disabled` and planning and
   replay routes fail closed.
2. Stop or drain new `planning` and `replay` jobs. Do not modify a terminal run;
   terminal inputs, explanations, selections, and scorecards are immutable.
3. Revert the BFF/worker application version if the defect is not flag-local.
   Leave the additive planning/replay tables in place so audit evidence and
   referential integrity are preserved.
4. Verify that no purchase, transfer, repair-routing, policy-writeback, or
   writeback-ledger entry was created by the advisory workflow.
5. Record the incident, affected tenant/run ids, safe error codes, and the
   decision required before re-enabling the tenant.

## Launch gates

- [x] Consumer contract locks versioned capability, submission, status, summary,
      coverage, solver evidence, and paged selection responses.
- [x] PostgreSQL tests prove tenant RLS, idempotency, immutable input/output,
      trusted replay universes, job lifecycle, and safe terminal failures.
- [x] Browser tests prove tenant-keyed caches, polling, stale/infeasible/failed
      states, drill-down reconciliation, replay caveats, and keyboard-accessible
      announcements/dialogs.
- [x] Adversarial tests prove disabled and cross-tenant planning/replay requests
      fail closed and client-authored historical facts cannot enter a scorecard.
- [x] The two-tenant 58,899-key benchmark fits the 15-minute planning window and
      returns explicit bounded-gap evidence instead of a heuristic downgrade.
- [x] The PostgreSQL lifecycle persists the exact 58,899-key result inside the
      same 15-minute window with bounded headers.
- [x] Full recommendation-engine, feature-store, agent-spine, PostgreSQL, and web
      regression gates are green on the final integrated source state.
- [x] A JWT-authenticated live-HTTP test crosses the BFF, PostgreSQL queues,
      production planning/replay workers, explanations, and scorecard retrieval
      while proving exact operational/writeback row images remain unchanged.
- [ ] The selected pilot tenant's repair coverage and TAT proxy limitations are
      reviewed with its planning lead.

## Final integrated regression

| Surface | Result |
|---|---:|
| Recommendation engine | 452 passed |
| Agent Spine, non-PostgreSQL | 501 passed, 3 skipped |
| Agent Spine, PostgreSQL | 273 passed, 2 env-gated skips |
| Feature store | 228 passed, 89 optional-integration skips |
| Forecasting | 71 passed |
| Nightly extract | 125 passed, 1 optional skip |
| Feature-store infrastructure | 16 passed |
| Web unit/component | 459 passed across 65 files |
| Web production build | Passed |
| Web lint | 0 errors; 3 pre-existing Fast Refresh warnings |
| Planning/replay browser E2E | 1 passed |
| Python Ruff gates | Passed |

Every engineering gate is closed. The feature flag remains default-off until
the selected pilot review, trusted replay-package import, and named operational
ownership are complete. Even after enablement, the decision remains
advisory-only.
