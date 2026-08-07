# Planning Calculation Trace Contract

**Date:** 2026-07-28
**Status:** Phase 1 implementation contract
**Scope:** Native extract or canonical upload → feature store → recommendation engine → tenant-scoped BFF → part drill-down

## Purpose

The planning calculation trace explains the quantities used for one
`tenant × part × location × horizon` calculation. It is evidence for an
advisory recommendation, not a second forecasting or policy implementation.
The browser receives the trace only through the tenant-scoped BFF route:

`GET /v1/tenants/{tenant_id}/parts/{pn}/{location}`

The recommendation engine and feature store remain private service boundaries.

## Selected recommendation routing

The part route accepts an optional `recommendation_id` query:

`GET /v1/tenants/{tenant_id}/parts/{pn}/{location}?recommendation_id={id}`

Workbench part and history links include the row's recommendation id. When it
is present, that exact recommendation drives `planning_trace`; proposed-policy
selection remains independently deterministic so an action-only recommendation
does not erase the key's policy proposal. Without the query, the existing
policy-first deterministic selection remains compatible.

An unknown id, an id for another key, and an id outside the resolved tenant all
produce the same tenant-scoped not-found response. Postgres seeds exact
per-recommendation traces beside the default part context and selects from that
stored map; it does not ignore the id or recompute forecasts online.

## Time conventions

- Observation windows are closed intervals. Exposure is
  `(observation_end - observation_start).days + 1`.
- Demand buckets with no observed events inside that interval are explicit zero
  periods for rate and dispersion calculations, including leading and trailing
  periods.
- A scheduled-demand item qualifies for horizon `H` when
  `as_of <= due_date <= as_of + H`, inclusive.
- An order that is still open in the source snapshot qualifies when
  `expected_receipt_date <= as_of + H`, inclusive. This includes overdue-open
  orders; the trace must distinguish them so a user does not interpret a late
  expected date as guaranteed supply.
- Boundary comparison uses dates in the source snapshot's documented business
  timezone. Persisted date-only values are never shifted by the browser.

## Demand semantics

Demand events and demanded units are different measures:

- `demand_event_count` counts source business events.
- `demanded_units` sums event quantities.
- A quantity greater than one changes units but does not create extra events.

Native and upload ingestion must preserve both measures. Legacy snapshots that
predate event-count fields may conservatively use one event per non-zero bucket.
That fallback is identified as `bucket_fallback`; it must not be presented as an
observed event count.

Legacy histories that predate configured observation bounds may use the closed
span from the first represented bucket through the last represented bucket so
existing advisory calculations remain available. That span is an
`observed_span` fallback and must produce a warning; it is never described as the
configured source window. Empty legacy history remains unavailable.

The historical rate is:

`historical_per_day = demanded_units / exposure_days`

This is a raw observation statistic. It is retained separately from
`served_historical_per_day`, which is the exact rate output by the selected
forecast model and used by the recommendation. Statistical models are allowed
to produce a served rate that differs from the raw observed rate.
`projection_kind` is labeled **Served distribution** in the UI (for example,
`NORMAL` or `COMPOUND_POISSON`); it is not forecast-model identity. Model
identity is a separate candidate/frontier concern introduced after Phase 1.

Known scheduled demand is not divided by the historical exposure window and is
not permanently added to the daily rate. It enters only a qualifying requested
horizon.

## Additive BFF view

`PartContext.planning_trace` is additive and default-safe for previously
persisted part-context JSON. For new recommendations, the BFF copies the
recommendation engine's immutable `CalculationEvidence` carrier field-for-field.
It does not reconstruct statistical projections, pooled/interchange totals,
repair receipts, availability, or net position from source snapshots.

```text
PlanningTraceView
  calculation_source:
    served_calculation | legacy_reconstructed | unavailable
  as_of: date | null
  horizon_end: date | null
  observation_start: date | null
  observation_end: date | null
  exposure_days: non-negative integer
  bucket: day | week | month | null
  observed_periods: non-negative integer
  zero_filled_periods: non-negative integer
  demand_event_count: non-negative integer | null
  event_count_source: observed | bucket_fallback | unavailable
  demanded_units: non-negative integer
  historical_per_day: non-negative number
  horizon_days: non-negative integer
  projection_kind: string | null
  served_historical_per_day: non-negative number | null
  projected_historical_demand: non-negative number
  scheduled_demand_status: available | partial | unavailable
  scheduled_demand_undated_lines: non-negative integer
  scheduled_demand_undated_units: non-negative integer
  scheduled_demand_due: non-negative number
  projected_demand: non-negative number | null
  dispatchable_available: non-negative number | null
  open_receipts_status: available | partial | unavailable
  open_receipts_undated_lines: non-negative integer
  open_receipts_undated_units: non-negative integer
  open_receipts_due: non-negative number
  overdue_open_receipts_due: non-negative number
  repair_receipts_due: non-negative number | null
  expected_receipts_due: non-negative number | null
  net_position: number | null
  shortage_before_action: non-negative number | null
  pooled_group_id: string | null
  pooling_scope: single_key | complete_group | worklist_partial
  excluded_member_keys: string[]
  members: MemberTrace[]
  constraints: ConstraintTrace[]
  warnings: string[]

MemberTrace
  pn: string
  location: string
  projection_kind: string
  projected_historical_demand: non-negative number
  scheduled_demand_status: available | partial | unavailable
  scheduled_demand_undated_lines: non-negative integer
  scheduled_demand_undated_units: non-negative integer
  scheduled_demand_due: non-negative number
  projected_demand: non-negative number
  dispatchable_available: non-negative number
  open_receipts_status: available | partial | unavailable
  open_receipts_undated_lines: non-negative integer
  open_receipts_undated_units: non-negative integer
  open_receipts_due: non-negative number
  overdue_open_receipts_due: non-negative number
  repair_receipts_due: non-negative number
  expected_receipts_due: non-negative number
  net_position: number

ConstraintTrace
  name: string
  value: string | null
  binding: boolean
  source: string
  scope: policy | action
```

When a source is unavailable, the BFF returns a typed unavailable state or a
warning. It does not invent a zero that could be mistaken for observed truth.
Existing response fields retain their meanings.

Constraint scope is explicit: `policy` affects target-level calculation, while
`action` affects the selected purchase/transfer/reduction action (for example,
an action minimum-order quantity or open-order deferral). Constraints persisted
before scope existed default to `policy`; the BFF does not infer another scope.

`calculation_source=served_calculation` means all served fields and at least one
member are present and exact. `legacy_reconstructed` is used only for a
carrier-less recommendation persisted before this contract; its historical,
scheduled-demand, and open-receipt values may be reconstructed from available
snapshots, while model/pooling/repair/net-position fields remain unavailable.
`unavailable` means no served recommendation calculation exists.

Scheduled-demand and open-receipt quantities each carry an independent
availability status at both aggregate and member grain:

- `available` means the corresponding source was present for the served key.
- `partial` means only partial source coverage was available and the quantity
  may be understated.
- `unavailable` means the quantity is not backed by source evidence.

A zero paired with `partial` or `unavailable` is never presented as an observed
zero. The BFF emits a warning and the UI displays the status beside the
quantity. Undated line/unit counts disclose evidence excluded from a dated
horizon. Pooled member status remains visible even when the aggregate group
contains a non-zero contribution from another member.

The quantity's provenance chip mirrors that status. `available` retains the
source's normal coverage/confidence, `partial` caps both at 65% and marks the
value derived, and `unavailable` sets coverage/confidence to zero. A green
provenance chip can therefore never sit beside a partial or unavailable badge.

Every numeric field in the engine carrier and BFF trace rejects `NaN`, positive
infinity, and negative infinity.

## Reconciliation invariants

For an exact served calculation:

```text
projected_historical_demand =
  served_historical_per_day
  × horizon_days

projected_demand =
  projected_historical_demand
  + scheduled_demand_due

expected_receipts_due =
  open_receipts_due
  + repair_receipts_due

net_position =
  dispatchable_available
  + expected_receipts_due
  - projected_demand

shortage_before_action =
  max(0, -net_position)
```

Every pooled total above, except `shortage_before_action`, equals the sum of the
corresponding member contribution. Undated line/unit totals also equal member
sums, and aggregate availability summarizes member states (`available` only
when all are available, `unavailable` only when all are unavailable, otherwise
`partial`). More than one member requires a `pooled_group_id`. Each member
independently reconciles projected demand, expected receipts, and net position.

`pooling_scope=complete_group` means all recognized interchange members were
evaluated and requires multiple member contributions. `worklist_partial` means
the solve was limited by the current worklist and requires explicit
`excluded_member_keys`; the BFF and UI warn that the displayed group is not
complete. `single_key` carries no excluded members.
Included `(pn, location)` member keys and excluded key labels are each unique
and mutually disjoint. `single_key` has exactly one member and no group id;
both pooled scopes require a group id.

The UI displays the server-returned operands and the server-returned result for
each equation. It may format or round each value for display, but it must not
calculate a result in the browser. Pooled member contributions remain visible so
the group total can be audited without inferring which parts or locations were
served.

## Phase 1 repair-receipt rule

Phase 1 intentionally gives aggregate in-repair stock no future supply credit.
`repair_receipts_due=0` is conservative and deliberate, not an observed promise
that no unit will return. Repair receipts remain zero until the application has
identity-aware repair orders and age-conditioned residual-time evidence that can
support a dated return. The BFF warning and part-detail methodology note disclose
this rule.

## Compatibility and isolation

- New feature and response fields have defaults that allow old snapshots to
  validate.
- A missing historical window is explicit; fixed implicit divisors are not used
  as observed truth.
- Tenant identity comes from the path plus authenticated tenant resolution,
  never from a request body or trace payload.
- Postgres-backed reads stay inside the existing tenant transaction/RLS
  boundary.
- The trace is read-only and cannot authorize a purchase, repair, transfer, or
  eMRO writeback.

## Required tests

- Configured 36-month exposure versus the former fixed 730-day divisor.
- Quantity greater than one while event count remains one.
- Leading, interior, and trailing zero periods.
- Inclusive observation, scheduled-demand, and receipt boundary dates.
- Overdue-open receipt disclosure.
- Statistical served-rate evidence that differs from the raw observed rate.
- Exact demand, receipt, net-position, and shortage reconciliation.
- Pooled/interchange totals and member contributions.
- Aggregate and per-member scheduled-demand/open-receipt availability states.
- Undated scheduled/open evidence counts and partial-worklist pooling disclosure.
- Explicit warnings for partial or unavailable zero-valued evidence.
- Conservative zero repair-receipt methodology disclosure.
- Rejection of non-finite numbers.
- Legacy event-count fallback and unavailable history.
- Explicit `legacy_reconstructed` and `unavailable` calculation states.
- Applied and binding constraint evidence.
- Local/snapshot/Postgres BFF contract parity and tenant isolation.
- Same-key multi-recommendation Workbench link-to-trace selection.
- Unknown, mismatched-key, and cross-tenant recommendation selection failures.
- Accessible full-trace and legacy/no-trace part-detail rendering.
