# Fast-Boot Feature-Store Snapshot — Design Spec

**Date:** 2026-07-02
**Owner:** Miguel Sosa
**Status:** Approved (design).

## Goal

Drop the Planner BFF container boot from **~190s to seconds** by loading a
precomputed, self-contained **snapshot of the built (pooled) feature store**
instead of re-parsing and re-pooling the 282MB raw extract at every boot. The
container stops needing the extract mount entirely.

This is the follow-up flagged by the 22.9K deploy (commit `49f94d9`): the
recommendations already load from a precomputed `recs.json` (15MB, fast), but
`PlannerStore.from_snapshot` still calls `build_stores_from_extract`, which
parses + network-pools 359K demand rows into 22.9K keys at boot
(`docker-compose.yml` healthcheck `start_period: 240s`).

## Measured facts (grounding, 2026-07-02)

- **Boot cost today:** ~190s in `build_stores_from_extract` (parse 282MB JSON +
  `pool_by_part` pooling). The engine run is already bypassed by
  `PLANNER_RECS_FILE`.
- **The feature store is a clean serialization target:**
  `InMemoryFeatureStore._data` is `{tenant_id: {bucket: {key_tuple: model}}}`
  with **12 buckets**; every value is a frozen pydantic v2 model
  (`ConfigDict(frozen=True, extra="forbid")`); `model_dump(mode="json")`
  round-trips (Decimal → string, date → ISO 8601).
- **Pooling shares object instances:** with `pool_by_part=True`, every planning
  key of a PN references the **same** pooled `DemandHistory` / `StockPosition`
  instance (~2.3 planning keys per PN at 22.9K keys). A naive per-key dump would
  multiply disk size *and*, after reload, container RAM by ~2.3× on the largest
  buckets.
- **`InMemoryInventoryState` is engine-only.** The fast-boot path never runs the
  engine, so the snapshot does not persist it.
- **The manifest is needed at boot:** `_build` reads `extract_dir/manifest.json`
  once (3.2MB) for the S7 feeds view; nothing else touches `EXTRACT_DIR` after
  seeding.
- **Version constraint:** the offline precompute host is Python 3.14, the
  container is 3.12 → artifacts must be **JSON, never pickle** (established
  Wave 3 constraint).

## Design

### 1. Serializer — `trax_io_feature_store/snapshot.py` (new, feature-store package)

The feature-store package owns `InMemoryFeatureStore._data`, so the serializer
lives there (no cross-package private access).

**Format v1 — one `feature_store.json`:**

```json
{
  "format": 1,
  "tenants": {
    "acme": {
      "demand_history": {
        "values":  [ { "...model_dump(mode='json')..." } ],
        "entries": [ [["PN123", "YYZ"], 0], [["PN123", "YUL"], 0] ]
      }
    }
  }
}
```

- `dump_store(store, path)` — per bucket, **intern values by object identity
  (`id()`)**: each unique model instance serializes once into `values[]`;
  `entries[]` maps every key tuple to its `values` index. The pooled-object
  sharing is thereby preserved on disk.
- `load_store(path) -> InMemoryFeatureStore` — per bucket, validate each unique
  value **once** against a module-level `_BUCKET_MODELS` map (bucket name → the
  12 pydantic model classes), then seed every entry pointing at the **shared
  instance** — restoring the in-memory sharing (RAM parity with `from_extract`).
- **Validation stays ON at load.** `extra="forbid"` + field validation makes
  schema drift between the py3.14 host and the py3.12 container fail loudly at
  boot, never silently.
- **Fail-loud errors:** unknown `format` version, bucket name not in
  `_BUCKET_MODELS`, or a value that fails validation → clear exception naming
  the bucket/artifact. No silent fallback.
- A **completeness test** pins `_BUCKET_MODELS.keys()` == the bucket names
  `InMemoryFeatureStore` constructs, so a future 13th bucket cannot silently
  not-snapshot.

### 2. Precompute batch — extend `bff/precompute.py`

After writing `recs.json` + `meta.json` (unchanged), the batch also writes into
the same `--out` dir:

- `feature_store.json` — via `dump_store` (the *pooled* store it already built);
- `keys.json` — the planning-key universe as a JSON array of `[pn, location]`
  pairs;
- `manifest.json` — copied from the extract dir (feeds view input);
- `meta.json` gains `snapshot_format: 1` (plus existing tenant / now /
  pool_by_part / projector / count / elapsed).

`--out` becomes a **complete snapshot dir**: everything the BFF needs at boot,
no extract required.

### 3. Load path — `PlannerStore.from_snapshot_dir` (new classmethod)

```python
PlannerStore.from_snapshot_dir(*, tenant_id, snapshot_dir, writeback=None) -> PlannerStore
```

1. Read `meta.json`; **fail loud if `meta["tenant"] != tenant_id`** or
   `snapshot_format` is unknown; missing artifact → clear error naming the file.
2. `fs = load_store(snapshot_dir / "feature_store.json")`.
3. Read `keys.json`, `recs.json` (existing verbatim `Recommendation.model_validate`
   path, reused), and the snapshot dir's `manifest.json`.
4. Delegate to the existing `_build(...)` — guardrails run over the loaded recs
   exactly as today (fast at 12K recs); `_entries`, `_manifest`,
   `_key_stats_cache` lazies, scenarios state all behave identically.

No engine, no extract parsing, no `InMemoryInventoryState`.

### 4. Wiring — `asgi.py` + `docker-compose.yml`

- `asgi.py` precedence: **`PLANNER_SNAPSHOT_DIR`** (new, highest) →
  `PLANNER_RECS_FILE` (existing recs-only snapshot; unchanged) → `from_extract`
  (default; sample extract dev flow unchanged).
- `docker-compose.yml` bff service: mount the snapshot dir read-only
  (`./deploy/_local_extract/emro_net_snapshot:/data/snapshot:ro`), set
  `PLANNER_SNAPSHOT_DIR=/data/snapshot`, **drop the 282MB extract mount and
  `PLANNER_RECS_FILE`**; shrink healthcheck `start_period` from 240s to the
  measured boot + margin.

### 5. Ops — regenerate, redeploy, measure (controller-run)

1. Re-run `trax-io-precompute` (pooled + statistical) over the real 22.9K
   extract with the extended batch → full snapshot dir. Record snapshot sizes.
2. Single sequential Docker rebuild + redeploy (project `trax-io-planner`;
   never touches `oracle19c`/MySQL).
3. Measure container boot (target: seconds to low tens); verify live
   dashboard / paged queue / part drill-down / feeds; then set the healthcheck
   `start_period` to reality.

## Testing

- **feature-store (`tests/`):** dump→load round-trip equality (per bucket, per
  key: `model_dump()` equality + exact model type); **sharing test** — two keys
  seeded with the *same* instance are still `is`-identical after reload;
  unknown-format / unknown-bucket / validation-failure error tests;
  `_BUCKET_MODELS` completeness test.
- **agent-spine BFF (`tests/bff/test_precompute.py` extension):** precompute
  writes the 3 new artifacts + meta field; **equivalence** — on the committed
  sample extract, `from_snapshot_dir` produces the same queue rows (stable-key
  comparison, same priority order), the same `dashboard()` output, and the same
  part-context for a sample key as `from_extract`; recs ids loaded verbatim;
  tenant-mismatch and missing-artifact failures.
- All existing suites stay green (BFF + agent-spine, feature-store, reco, UI).
- Boot-time improvement is **measured at deploy** (ops step), not unit-tested.

## Out of scope

- Compression / parquet / sqlite (plain JSON is debuggable; disk isn't the
  constraint — boot time is).
- Content-hash dedup beyond identity interning (identity captures the pooling
  sharing exactly).
- Auto-fallback to the slow path on load error (fail loud; ops can unset
  `PLANNER_SNAPSHOT_DIR` to boot the old way).
- Incremental / partial snapshot updates; nightly rotation.
- The full-62K run (separate follow-up; it regenerates its snapshot with this
  same machinery and inherits the fast boot).

## Risks

- **Snapshot size unmeasured** until the ops step; expected well under the
  282MB extract (demand interned to ~1 series/PN, closed-orders already reduced
  to lead-time distributions). Mitigation: the ops step measures and records
  sizes; if `json.load` itself dominates, revisit compression (out of scope now).
- **Load-time validation cost at 62K** keys is unproven; at 22.9K it validates
  ~1 unique demand series per PN (≈10K) rather than per key. Measure at deploy.
- **Schema evolution invalidates snapshots by design** — a feature-model field
  change makes old snapshots fail loudly at boot; the remedy is re-running the
  precompute. Acceptable for a single-artifact local deploy.
- **Cross-package edit**: agent-spine consumes feature-store via a non-editable
  path source → `uv sync --extra bff --reinstall` after editing feature-store
  (established lesson).
