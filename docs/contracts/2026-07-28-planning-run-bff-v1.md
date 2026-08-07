# Advisory Planning Run BFF Contract v1

**Date:** 2026-07-28
**Status:** Consumer-locked implementation contract
**Scope:** Tenant-scoped planning and historical replay browser surfaces

## Safety boundary

Planning runs and historical replay are advisory-only. The public routes expose
no approve, commit, purchase, transfer, repair-route, policy-update, or
writeback operation. `advisory_only=true` is evidence of this boundary, not an
authorization grant.

Both surfaces are default-off. A tenant is available only when its canonical
slug is in `PLANNING_ENABLED_TENANTS`. Verified tenant claims are still required,
and only `planner`, `admin`, or `owner` may submit a planning or replay run. A
viewer can read enabled-tenant evidence but cannot submit.

## Planning resources

```text
GET  /v1/tenants/{tenant}/planning-runs/capabilities
POST /v1/tenants/{tenant}/planning-runs
GET  /v1/tenants/{tenant}/planning-runs
GET  /v1/tenants/{tenant}/planning-runs/{run_id}
GET  /v1/tenants/{tenant}/planning-runs/{run_id}/rerun-config
GET  /v1/tenants/{tenant}/planning-runs/{run_id}/selections
```

The submission body contains bounded planner assumptions:

- `scope_kind=all_eligible` sends no browser-authored keys. The server resolves
  the tenant's authoritative eligible universe in one trusted snapshot read.
- `scope_kind=explicit` is a preview of at most 200 canonically ordered,
  tenant-validated `PN@LOCATION` keys.
- Budget is a non-negative decimal with at most 18 digits and two decimal
  places. Horizon is 1–3,650 days. Objective and floor decimals have at most 18
  digits and six decimal places; service/AOG thresholds remain in `[0,1]`.
- Currency, bounded objective weights, at most 500 planner floors across at
  most 200 keys, optional parent UUID, and a solver limit greater than zero and
  no more than the core `MAX_PLANNING_SOLVER_SECONDS` value of 600 seconds.

The browser never sends candidate menus. The BFF resolves immutable, versioned
candidate inputs and the tenant planning store persists them separately from
the bounded run header. An identical immutable input returns the existing run.

## Bounded run header

List, submit, and detail responses expose a bounded `PlanningRunView`:

- immutable ids, lineage, status, submitted snapshot and common source
  generation hashes, budget, horizon, currency, model profile, progress,
  timestamps, and advisory marker;
- scope kind, exact key count, and at most 20 preview labels;
- terminal portfolio summary, bounded infeasibility counts/samples, and solver
  evidence;
- aggregate warning/skipped counts and at most 50 normalized reason codes;
- bounded immutable-menu coverage and explicit repair/TAT availability;
- safe error code, retryability, attempt count, and bounded operator guidance;
- additive staleness evidence only when an O(1) trusted current-generation
  marker exists. Explicit and all-eligible runs compare the same generation
  marker, while snapshot-hash comparison is exposed only when scopes are
  exactly comparable.

The public header must never contain `request`, `menus`, `explicit_scope`,
terminal `result.selections`, `selection_details`, observations, raw worker or
database errors, or payload-derived telemetry labels. Staleness polling must
not call `part_context` per key.

Completed selections are a separate server-paged resource. The store applies
tenant scope, optional exact decision-key and no-change filters, stable
`ORDER BY decision_key`, `LIMIT`, and `OFFSET`, and returns the filtered count.
The BFF does not load, sort, or filter the full selection set in memory.

## Saved reruns

The terminal-only `rerun-config` resource exposes the bounded, browser-editable
parent configuration: scope kind, at most 200 explicit keys, budget, horizon,
currency, objective weights, planner-authored floor overrides, solver limit,
source generation, and parent/current trusted model profiles.

- `all_eligible` remains server-resolved. Its rerun configuration contains no
  expanded key universe and never serializes system-derived menu floors; only
  bounded browser-authored overrides may return.
- The browser can prefill saved assumptions and submit a parent UUID, but it
  cannot choose a forecast, repair, candidate-planner, optimizer, or arbitrary
  artifact version.
- A rerun always resolves the current trusted model profile. When the trusted
  repair model differs from the parent, the UI shows both versions and
  requires explicit acknowledgement of “Use current trusted repair
  assumptions.”
- An unchanged parent with no trusted repair change is a blocked/no-op. A real
  child persists parent lineage, a new planning fingerprint, and a bounded
  assumption diff.

## Status semantics

- `queued` and `running` return progress only. The browser polls these states.
- `completed` returns an aggregate summary and reads detail rows only through
  the paged selections resource.
- `infeasible` returns no actionable selections. Minimum required budget,
  shortfall, and bounded key/floor samples are evidence, not an automatic
  policy override.
- `failed` returns a safe error code and guidance, never raw infrastructure
  text.
- `stale=true` means newer trusted inputs are available. The submitted run and
  its immutable snapshot remain unchanged and reproducible.
- Solver termination `not_proven` remains visible with bound/gap evidence; it
  must not be relabeled optimal.

Structured HTTP failures use
`{"detail":{"code","message","retryable"}}`. Codes and telemetry dimensions are
bounded. Tenant ids, principals, decision keys, candidate ids, raw error text,
and request payloads are prohibited as metric labels.

Pydantic validation failures on planning and replay paths use that same locked
envelope with `planning_request_invalid` or `replay_request_invalid`. The
response never returns Pydantic's rejected `input` value or raw validation
detail.

## Operational event sink

The BFF and worker emit one compact JSON object as the log message for every
operational event. Dimensions therefore survive Python's default logging
formatter and are directly collectable from application stdout without a
vendor-specific in-process exporter. The same bounded fields remain attached
to the `LogRecord` for structured logging backends.

- `ingest_validation_terminal` covers validation errors, PO/RO/unknown
  classification, legacy classification fallback, NEW/REP configured fallback
  and unavailable counts, repair-order/serial deduplication, coverage counts,
  status, and worker duration.
- `planning_worker_terminal` covers candidate and key counts, worker and solver
  duration, feasibility, solver termination and gap, reconciliation, and
  terminal status.
- `planning_worker_failure` adds bounded attempt, terminal, stage, and exception
  type dimensions while excluding raw exception text.
- `planning_http_request`, `planning_run_observed`, and
  `planning_submission` cover bounded operation/outcome latency, run state,
  staleness, solver state, and created-versus-reused submissions.

No event contains tenant, principal, run/job/decision/candidate identity, raw
errors, or request payloads. Runtime log collection is the durable sink; the
in-process telemetry snapshot exists only for deterministic tests and local
diagnostics.

## Historical replay

```text
GET  /v1/tenants/{tenant}/replay-runs/capabilities
POST /v1/tenants/{tenant}/replay-runs
GET  /v1/tenants/{tenant}/replay-runs
GET  /v1/tenants/{tenant}/replay-runs/universes
GET  /v1/tenants/{tenant}/replay-runs/{replay_id}
GET  /v1/tenants/{tenant}/replay-runs/{replay_id}/lineage
GET  /v1/tenants/{tenant}/replay-runs/{replay_id}/exclusions
GET  /v1/tenants/{tenant}/replay-runs/{replay_id}/cohorts
```

Replay uses the same default-off tenant flag. The browser submits only a
bounded configuration plus an opaque `universe_ref`, policy labels, comparison
rule, currency, and a non-negative tolerance with at most 18 digits and 12
decimal places. In production, the PostgreSQL submission owns the single
tenant-scoped universe resolution and fingerprint transaction; the BFF does
not duplicate a full trusted-request preflight. Unknown and cross-tenant
references return the same safe 422 response. Client-authored observations,
metrics, scorecards, or lineage are rejected.

The server-paged `universes` resource returns at most 100 opaque metadata rows:
bounded reference/id, public universe hash, contract version, currency,
declared/observed/excluded counts, and creation time. It never returns the
trusted-input digest or any historical observation, outcome, decision, or
lineage row. The route itself and the planner UI require `planner`, `admin`, or
`owner`; viewers remain evidence-only and never load approved package
references or counts. An empty page explains that external data-pipeline
package generation and service-role import are launch dependencies.

List, submit, and detail return only a bounded replay header and aggregate
scorecard. Lineage, exclusion, and cohort collections are separate resources
with stable server paging, a maximum page size of 100, bounded filters, and
filtered totals. The header contains counts and integrity digests instead of
embedding those collections.

Replay remains shadow evidence: matched-universe coverage, exclusions,
cohorts, metric definitions, and plan lineage do not create operational
authority or prove causal future performance.

## Consumer gates

`services/agent-spine/tests/bff/test_planning_consumer_contract.py` and replay
OpenAPI assertions lock route shape, bounded request limits, response models,
and the absence of writeback operations. Route tests lock tenant/role
isolation, feature flags, authoritative scope resolution, numeric
exponent/precision limits, response-size safety, paging, generation staleness,
structured failures, and low-cardinality telemetry. Browser tests lock
tenant-keyed caches during active polling and mutations, active-only polling,
full-portfolio default submission, saved-rerun prefill/no-op/trusted-repair
semantics, fieldset legends, solver caveats, safe failure rendering, opaque
universe selection, planner-only replay submission, and replay methodology.
