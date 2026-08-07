# Trusted replay universe import

Historical replay facts are imported only through the service seed role. The
browser can list bounded metadata and submit an opaque `universe_ref`, but it
cannot author observations, exclusions, lineage, outcomes, source hashes, or
planning fingerprints.

Source extraction remains owned by the controlled data pipeline. Repository
code now turns those explicit historical records into the matched-evaluation
package; neither the builder nor the importer queries a current snapshot or
fabricates a missing fact. Keep the tenant replay feature flag off until a
reviewed, approved package has been built and successfully imported.

Prepare a UTF-8 `replay-source.v1` JSON file for
`TrustedReplaySourcePackage`. Every `matched` source record contains:

- the decision identity, `as_of`, and complete evaluation horizon;
- exactly one shared factual record for demand, receipts, repair lifecycle
  outcomes, effective price, and part attributes;
- exactly one per-policy record for model artifacts, tenant policy,
  objective configuration, and candidate-frontier configuration;
- explicit source hashes plus `occurred_at` and `available_at` timestamps;
- realized metrics and a complete, content-hashed outcome manifest; and
- the composite immutable planning-selection link
  `(tenant_id, planning_run_id, planning_selection_decision_key)` plus the
  time that selection became available.

The planning link can resolve through the active planning repository or the
approved immutable archive; it deliberately is not a local database foreign
key. It is part of the replay request identity and canonical observation
lineage digest, so changing the run or selection changes the trusted package.
Legacy `replay.v1` packages without the optional link remain readable, while
the `replay-source.v1` matched-record builder requires it.

For every decision input, both `occurred_at` (when applicable) and
`available_at` must be at or before the decision `as_of`. The same cutoff
applies to the linked planning selection. The builder never emits a matched
observation containing a later demand, receipt, repair outcome, price, model,
or configuration fact. It instead emits an explicit `invalid_lineage`
exclusion whose detail starts with
`no_lookahead_cutoff_violation:` and names the sorted source domains. Known
missing or incomplete decisions must be supplied as `excluded` source records
using one of the stable `ReplayExclusion` reason codes. Realized outcomes are
different: they must cover the complete `[as_of, horizon_end]` window and
cannot be marked available before that window ends.

Unless a reviewed evaluation design says otherwise, omit the optional
comparison settings to use the repository defaults: labels `current` and
`repair-aware`, rule `matched_budget`, and exact zero match tolerance.

Build the contract-validated, import-ready package locally in the controlled
pipeline environment:

```bash
trax-io-replay-build \
  --input "/secure/path/replay-source-v1.json" \
  --output "/secure/path/replay-evaluation-request.json"
```

The output is a complete `ReplayEvaluationRequest`, including the exact
decision universe, matched observations or explicit exclusions, no-lookahead
lineage, and realized outcomes. Import that output with:

```bash
trax-io-replay-import \
  --tenant-uuid "00000000-0000-0000-0000-000000000000" \
  --universe-ref "2026-q2-approved-shadow-package" \
  --input "/secure/path/replay-evaluation-request.json"
```

Set `WORKER_DATABASE_URL` (preferred) or `DATABASE_URL` in the process
environment; do not place service credentials in command arguments or shell
history. The URL must authenticate as the BYPASSRLS `trax_seed` service role
(or an equivalently privileged controlled operator role). The tenant UUID
is explicit and must resolve to the request’s tenant slug. Imports are
fail-closed, capped at 512 MiB, fully contract-validated before any write,
immutable after the declared row count is reached, and idempotent when the same
reference names byte-equivalent canonical evidence. Reusing a reference for
different evidence fails without modifying the existing universe.

Do not expose either command, the source schema, or historical fact payloads
through browser routes. Browsers receive bounded universe metadata and submit
only the opaque approved `universe_ref` plus bounded comparison configuration.
