# Fast-Boot Feature-Store Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drop Planner-BFF container boot from ~190s to seconds by persisting the built (pooled) `InMemoryFeatureStore` to JSON at precompute time and loading it at boot, instead of re-parsing + re-pooling the 282MB extract.

**Architecture:** A new `snapshot.py` in the feature-store package serializes `InMemoryFeatureStore._data` per bucket as identity-interned `values[]` + `entries[]` (pooling shares one model instance across ~2.3 planning keys/PN — interning preserves that on disk and, on load, in RAM). `trax-io-precompute`'s `--out` dir becomes a complete snapshot (feature_store.json + keys.json + manifest.json + recs.json + meta.json); a new `PlannerStore.from_snapshot_dir` boots from it with no extract parsing, no pooling, no engine; `asgi.py` selects it via `PLANNER_SNAPSHOT_DIR`.

**Tech Stack:** Python ≥3.12, pydantic v2 (`model_dump(mode="json")` / `model_validate`), pytest, uv; FastAPI TestClient for the asgi test; Docker compose (project `trax-io-planner`) for the ops step.

**Spec:** [docs/superpowers/specs/2026-07-02-fast-boot-feature-store-snapshot-design.md](../specs/2026-07-02-fast-boot-feature-store-snapshot-design.md)

## Global Constraints

- **JSON, never pickle** — the offline precompute host is Python 3.14, the container is 3.12.
- **Validation ON at load; fail loud** — no silent fallback to the slow path; unknown format/bucket or a validation failure raises with the bucket/artifact named.
- **Docker scoped to project `trax-io-planner` only; NEVER touch the `oracle19c`/`oracle` or MySQL containers; single sequential builds.**
- **Real extract/snapshot data stays gitignored** (`deploy/_local_extract/`).
- **Cross-package rule:** after editing `services/feature-store`, refresh agent-spine's venv: `cd services/agent-spine && uv sync --extra bff --reinstall` (editable path deps don't pick up changes otherwise).
- **All suites stay green:** feature-store `uv run --extra dev pytest`; agent-spine `uv run --extra bff pytest`; `uv run --extra dev ruff check .` in both.
- **Commit prefix `#7`** (the real-eMRO pipeline arc), lines ≤100 chars (repo ruff config).

## File Structure

- Create: `services/feature-store/src/trax_io_feature_store/snapshot.py` — dump/load + `_BUCKET_MODELS` + `SnapshotFormatError` (owns the format; same package as `InMemoryFeatureStore._data`)
- Create: `services/feature-store/tests/test_snapshot.py`
- Modify: `services/agent-spine/src/trax_io_spine/bff/precompute.py` — write the 3 new artifacts + meta fields
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py` — add `from_snapshot_dir`
- Modify: `services/agent-spine/src/trax_io_spine/bff/asgi.py` — `PLANNER_SNAPSHOT_DIR` precedence; env reads move inside `build_app()` (makes it testable)
- Modify: `services/agent-spine/tests/bff/test_precompute.py` — snapshot-artifact + `from_snapshot_dir` tests
- Create: `services/agent-spine/tests/bff/test_asgi.py`
- Modify (ops task): `docker-compose.yml`, `ROADMAP.md`, `TASKS.md`, `CLAUDE.md`

---

### Task 1: `snapshot.py` — interned JSON dump/load for `InMemoryFeatureStore`

**Files:**
- Create: `services/feature-store/src/trax_io_feature_store/snapshot.py`
- Test: `services/feature-store/tests/test_snapshot.py`

**Interfaces:**
- Consumes: `InMemoryFeatureStore` (`trax_io_feature_store.client` — `._data`, `.seed(tenant_id, bucket, key, value)`, `._BUCKETS`); the 12 feature models from `trax_io_feature_store.schemas.features`.
- Produces (Tasks 2–3 rely on these exact names):
  - `SNAPSHOT_FORMAT: int = 1`
  - `dump_store(store: InMemoryFeatureStore, path: str | Path) -> dict[str, int]` (returns `{"tenants", "buckets", "entries", "unique_values"}` counts)
  - `load_store(path: str | Path) -> InMemoryFeatureStore`
  - `SnapshotFormatError(ValueError)`

- [ ] **Step 1: Write the failing tests**

Create `services/feature-store/tests/test_snapshot.py`:

```python
"""JSON snapshot round-trip for InMemoryFeatureStore (fast BFF boot).

Locks the three load-bearing properties of the snapshot format:
(1) lossless round-trip (same model types + payloads per tenant/bucket/key),
(2) identity interning — pooled objects shared across keys stay ONE instance
    after reload (disk size and container RAM both depend on this),
(3) fail-loud contract — unknown format/bucket, non-model values, and
    validation failures raise SnapshotFormatError naming the culprit.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from trax_io_feature_store.client import InMemoryFeatureStore
from trax_io_feature_store.schemas.features import (
    CurrentPolicy,
    DemandHistory,
    DemandObservation,
    StockPosition,
    VendorEconomics,
)
from trax_io_feature_store.snapshot import (
    SNAPSHOT_FORMAT,
    _BUCKET_MODELS,
    SnapshotFormatError,
    dump_store,
    load_store,
)

_D = date(2024, 4, 1)


def _sample_store() -> InMemoryFeatureStore:
    store = InMemoryFeatureStore()
    store.seed(
        "acme", "stock_position", ("PN1", "YYZ"),
        StockPosition(
            tenant_id="acme", pn="PN1", location="YYZ",
            on_hand=5, serviceable=4, extract_date=_D,
        ),
    )
    store.seed(
        "acme", "current_policy", ("PN1", "YYZ"),
        CurrentPolicy(
            tenant_id="acme", pn="PN1", location="YYZ",
            rop=2, eoq=3, safety_stock=1, max_stock=6, extract_date=_D,
        ),
    )
    store.seed(
        "acme", "vendor_economics", ("PN1", "V1"),
        VendorEconomics(
            tenant_id="acme", pn="PN1", vendor="V1",
            unit_cost=Decimal("12.34"), extract_date=_D,
        ),
    )
    demand = DemandHistory(
        tenant_id="acme", pn="PN1", location="YYZ",
        observations=[
            DemandObservation(bucket="month", period_start=date(2024, 3, 1), removals=2, issues=1)
        ],
        extract_date=_D,
    )
    # Pooling assigns the SAME instance to every planning key of a PN — model that here.
    store.seed("acme", "demand_history", ("PN1", "YYZ"), demand)
    store.seed("acme", "demand_history", ("PN1", "YUL"), demand)
    # Second tenant proves the tenant dimension round-trips.
    store.seed(
        "beta", "stock_position", ("PN9", "LHR"),
        StockPosition(
            tenant_id="beta", pn="PN9", location="LHR",
            on_hand=1, serviceable=1, extract_date=_D,
        ),
    )
    return store


def test_round_trip_preserves_types_and_payloads(tmp_path):
    store = _sample_store()
    stats = dump_store(store, tmp_path / "fs.json")
    loaded = load_store(tmp_path / "fs.json")

    assert set(loaded._data) == set(store._data)
    for tenant_id, buckets in store._data.items():
        assert set(loaded._data[tenant_id]) == set(buckets)
        for bucket, entries in buckets.items():
            loaded_entries = loaded._data[tenant_id][bucket]
            assert set(loaded_entries) == set(entries)
            for key, value in entries.items():
                got = loaded_entries[key]
                assert type(got) is type(value)
                assert got.model_dump() == value.model_dump()
    # Decimal survives the mode="json" string round-trip.
    ve = loaded._data["acme"]["vendor_economics"][("PN1", "V1")]
    assert ve.unit_cost == Decimal("12.34")
    assert stats == {"tenants": 2, "buckets": 5, "entries": 6, "unique_values": 5}


def test_shared_instances_stay_shared_after_reload(tmp_path):
    dump_store(_sample_store(), tmp_path / "fs.json")
    loaded = load_store(tmp_path / "fs.json")
    a = loaded._data["acme"]["demand_history"][("PN1", "YYZ")]
    b = loaded._data["acme"]["demand_history"][("PN1", "YUL")]
    assert a is b


def test_bucket_models_cover_every_store_bucket():
    assert set(_BUCKET_MODELS) == set(InMemoryFeatureStore._BUCKETS)


def test_unknown_format_version_fails_loud(tmp_path):
    (tmp_path / "fs.json").write_text(json.dumps({"format": 99, "tenants": {}}))
    with pytest.raises(SnapshotFormatError, match="format"):
        load_store(tmp_path / "fs.json")


def test_unknown_bucket_fails_loud(tmp_path):
    payload = {
        "format": SNAPSHOT_FORMAT,
        "tenants": {"acme": {"not_a_bucket": {"values": [], "entries": []}}},
    }
    (tmp_path / "fs.json").write_text(json.dumps(payload))
    with pytest.raises(SnapshotFormatError, match="not_a_bucket"):
        load_store(tmp_path / "fs.json")


def test_validation_failure_names_the_bucket(tmp_path):
    payload = {
        "format": SNAPSHOT_FORMAT,
        "tenants": {
            "acme": {
                "stock_position": {
                    "values": [{"tenant_id": "acme", "pn": "PN1"}],  # missing required fields
                    "entries": [[["PN1", "YYZ"], 0]],
                }
            }
        },
    }
    (tmp_path / "fs.json").write_text(json.dumps(payload))
    with pytest.raises(SnapshotFormatError, match="stock_position"):
        load_store(tmp_path / "fs.json")


def test_dump_rejects_non_model_values(tmp_path):
    store = InMemoryFeatureStore()
    store.seed("acme", "stock_position", ("PN1", "YYZ"), {"on_hand": 5})
    with pytest.raises(SnapshotFormatError, match="stock_position"):
        dump_store(store, tmp_path / "fs.json")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/feature-store"
uv run --extra dev pytest tests/test_snapshot.py -v
```

Expected: FAIL at import — `ModuleNotFoundError: No module named 'trax_io_feature_store.snapshot'`.

- [ ] **Step 3: Write the implementation**

Create `services/feature-store/src/trax_io_feature_store/snapshot.py`:

```python
"""JSON snapshot persistence for `InMemoryFeatureStore` (fast BFF boot).

`dump_store` serializes a built (typically network-pooled) store to one JSON file;
`load_store` rebuilds an equivalent store. Values are interned by object identity:
pooling assigns the SAME `DemandHistory`/`StockPosition` instance to every planning
key of a PN (~2.3 keys/PN in real eMRO data), so per-bucket `values[]` holds each
unique model once and `entries[]` maps key tuples to indices — preserving disk size
AND, on load, the in-memory sharing (RAM parity with a freshly built store).

JSON, not pickle: the offline precompute host and the container may run different
Python versions (3.14 vs 3.12). Validation stays ON at load — `extra="forbid"` on the
feature models makes schema drift fail loudly at boot, never silently.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from trax_io_feature_store.client import InMemoryFeatureStore
from trax_io_feature_store.schemas.features import (
    CausalUtilization,
    Criticality,
    CurrentPolicy,
    DemandHistory,
    InterchangeableGraph,
    LeadTimeDistribution,
    LocationGraph,
    OpenOrdersSnapshot,
    PartAttributes,
    StockPosition,
    VendorEconomics,
    WashRateHistory,
)

SNAPSHOT_FORMAT = 1

# One entry per InMemoryFeatureStore bucket; test_snapshot.py pins this map against
# `InMemoryFeatureStore._BUCKETS` so a future 13th bucket cannot silently not-snapshot.
_BUCKET_MODELS: dict[str, type[BaseModel]] = {
    "demand_history": DemandHistory,
    "causal_utilization": CausalUtilization,
    "lead_time_distribution": LeadTimeDistribution,
    "wash_rate_history": WashRateHistory,
    "vendor_economics": VendorEconomics,
    "part_attributes": PartAttributes,
    "criticality": Criticality,
    "interchangeable_graph": InterchangeableGraph,
    "location_graph": LocationGraph,
    "open_orders_snapshot": OpenOrdersSnapshot,
    "stock_position": StockPosition,
    "current_policy": CurrentPolicy,
}


class SnapshotFormatError(ValueError):
    """A snapshot cannot be dumped/loaded: unknown format version, unknown bucket,
    non-model value at dump time, or a value that fails model validation at load."""


def dump_store(store: InMemoryFeatureStore, path: str | Path) -> dict[str, int]:
    """Serialize `store` to `path`. Returns counts for operator logging."""
    tenants: dict[str, dict] = {}
    n_buckets = n_entries = n_values = 0
    for tenant_id, buckets in store._data.items():
        tenant_payload: dict[str, dict] = {}
        for bucket, entries in buckets.items():
            values: list[dict] = []
            index_by_id: dict[int, int] = {}
            out_entries: list[list] = []
            for key, value in entries.items():
                if not isinstance(value, BaseModel):
                    raise SnapshotFormatError(
                        f"bucket {bucket!r} key {key!r}: expected a pydantic model, "
                        f"got {type(value).__name__}"
                    )
                idx = index_by_id.get(id(value))
                if idx is None:
                    idx = len(values)
                    index_by_id[id(value)] = idx
                    values.append(value.model_dump(mode="json"))
                out_entries.append([list(key), idx])
            tenant_payload[bucket] = {"values": values, "entries": out_entries}
            n_buckets += 1
            n_entries += len(out_entries)
            n_values += len(values)
        tenants[tenant_id] = tenant_payload
    Path(path).write_text(json.dumps({"format": SNAPSHOT_FORMAT, "tenants": tenants}))
    return {
        "tenants": len(tenants),
        "buckets": n_buckets,
        "entries": n_entries,
        "unique_values": n_values,
    }


def load_store(path: str | Path) -> InMemoryFeatureStore:
    """Rebuild an `InMemoryFeatureStore` from a `dump_store` file.

    Each unique value validates ONCE; entries sharing an index share one model
    instance (exactly as pooling built them). Fail-loud on any drift.
    """
    raw = json.loads(Path(path).read_text())
    fmt = raw.get("format") if isinstance(raw, dict) else None
    if fmt != SNAPSHOT_FORMAT:
        raise SnapshotFormatError(f"unsupported snapshot format: {fmt!r}")
    store = InMemoryFeatureStore()
    for tenant_id, buckets in raw["tenants"].items():
        for bucket, payload in buckets.items():
            model_cls = _BUCKET_MODELS.get(bucket)
            if model_cls is None:
                raise SnapshotFormatError(f"unknown feature bucket in snapshot: {bucket!r}")
            try:
                instances = [model_cls.model_validate(v) for v in payload["values"]]
            except ValidationError as exc:
                raise SnapshotFormatError(
                    f"snapshot bucket {bucket!r}: a value failed "
                    f"{model_cls.__name__} validation"
                ) from exc
            for key, idx in payload["entries"]:
                store.seed(tenant_id, bucket, tuple(key), instances[idx])
    return store
```

Note: `store._data` access is same-package by design (this module owns the format for the store that owns the dict). If ruff flags `SLF001`, it is not enabled in this repo's config today — do not add noqa unless `ruff check` actually complains.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run --extra dev pytest tests/test_snapshot.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Run the full feature-store suite + lint**

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
```

Expected: all pass, 7 new (some iceberg/dynamodb tests skip without their extras — that skip pattern predates this task), ruff clean.

- [ ] **Step 6: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/feature-store/src/trax_io_feature_store/snapshot.py services/feature-store/tests/test_snapshot.py
git commit -m "#7 feature-store: interned JSON snapshot (dump_store/load_store) for fast BFF boot"
```

---

### Task 2: Precompute writes the complete snapshot dir

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/precompute.py`
- Test: `services/agent-spine/tests/bff/test_precompute.py` (append one test)

**Interfaces:**
- Consumes: `dump_store`, `SNAPSHOT_FORMAT` from `trax_io_feature_store.snapshot` (Task 1).
- Produces: `--out` dir now contains `feature_store.json`, `keys.json` (JSON array of `[pn, location]` pairs), `manifest.json` (copied from the extract dir when present), alongside the existing `recs.json` + `meta.json`; `meta.json` gains `"snapshot_format": 1` and `"keys": <int>`. Task 3's `from_snapshot_dir` reads exactly these five filenames.

- [ ] **Step 1: Refresh the path dep so agent-spine sees Task 1's new module**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/agent-spine"
uv sync --extra bff --reinstall
```

Expected: resolves + reinstalls `trax-io-feature-store` (and the other path deps) without error.

- [ ] **Step 2: Write the failing test**

Append to `services/agent-spine/tests/bff/test_precompute.py`:

```python
def test_precompute_writes_full_snapshot_dir(tmp_path):
    # Fast-boot slice: --out is a COMPLETE snapshot dir — feature store, keys
    # universe, and manifest land next to recs.json/meta.json so the BFF can boot
    # with no extract dir at all (PlannerStore.from_snapshot_dir).
    out_dir, meta = _precompute(tmp_path)

    for artifact in ("feature_store.json", "keys.json", "manifest.json"):
        assert (out_dir / artifact).exists(), f"missing {artifact}"

    assert meta["snapshot_format"] == 1
    assert meta["keys"] > 0

    keys = json.loads((out_dir / "keys.json").read_text())
    assert isinstance(keys, list)
    assert len(keys) == meta["keys"]
    assert all(isinstance(k, list) and len(k) == 2 for k in keys)

    fs_raw = json.loads((out_dir / "feature_store.json").read_text())
    assert fs_raw["format"] == 1
    assert "acme" in fs_raw["tenants"]
    assert "stock_position" in fs_raw["tenants"]["acme"]
```

- [ ] **Step 3: Run it to verify it fails**

```bash
uv run --no-sync --extra bff pytest tests/bff/test_precompute.py::test_precompute_writes_full_snapshot_dir -v
```

Expected: FAIL — `AssertionError: missing feature_store.json`.

- [ ] **Step 4: Implement**

In `services/agent-spine/src/trax_io_spine/bff/precompute.py`:

Add imports (after the existing `from pathlib import Path`):

```python
import shutil
```

and (with the other `trax_io_feature_store` import):

```python
from trax_io_feature_store.snapshot import SNAPSHOT_FORMAT, dump_store
```

In `run()`, replace the block from `recs_payload = ...` through `(out_dir / "meta.json").write_text(json.dumps(meta))` with:

```python
    recs_payload = [rec.model_dump(mode="json") for rec in batch.recommendations]
    (out_dir / "recs.json").write_text(json.dumps(recs_payload))

    # Fast-boot snapshot: persist the BUILT (pooled) feature store + the keys universe
    # + the manifest, so PlannerStore.from_snapshot_dir boots with no extract parsing,
    # no pooling, and no engine run (spec: 2026-07-02-fast-boot-feature-store-snapshot).
    stats = dump_store(fs, out_dir / "feature_store.json")
    (out_dir / "keys.json").write_text(json.dumps([list(k) for k in keys]))
    manifest_src = Path(args.extract_dir) / "manifest.json"
    if manifest_src.exists():
        shutil.copyfile(manifest_src, out_dir / "manifest.json")

    elapsed = time.monotonic() - started
    meta = {
        "tenant": tenant_id,
        "now": now.isoformat(),
        "pool_by_part": args.pool_by_part,
        "projector": args.projector,
        "count": len(recs_payload),
        "keys": len(keys),
        "snapshot_format": SNAPSHOT_FORMAT,
        "elapsed_seconds": round(elapsed, 3),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta))

    print(
        f"precomputed {meta['count']} recommendations + feature-store snapshot "
        f"({stats['unique_values']} unique values / {stats['entries']} entries) "
        f"in {elapsed:.2f}s -> {out_dir}"
    )
    return meta
```

Also update the module docstring's first paragraph: replace the sentence beginning `This CLI runs that computation offline and writes` with:

```
This CLI runs that computation offline and writes a complete snapshot dir:
`recs.json` (a JSON array of `Recommendation.model_dump(mode="json")`),
`feature_store.json` (the built, pooled feature store — see
`trax_io_feature_store.snapshot`), `keys.json` (the planning-key universe),
`manifest.json` (copied for the feeds view), and `meta.json` (run metadata).
`PlannerStore.from_snapshot_dir` (in `store.py`) boots from that dir with no
extract parsing at all; the older `from_snapshot` (recs-only) path still works.
```

- [ ] **Step 5: Run the precompute tests**

```bash
uv run --no-sync --extra bff pytest tests/bff/test_precompute.py -v
```

Expected: 5 passed (4 existing — the `on_disk_meta == meta` assertion tolerates the two new meta fields — + 1 new).

- [ ] **Step 6: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/agent-spine/src/trax_io_spine/bff/precompute.py services/agent-spine/tests/bff/test_precompute.py
git commit -m "#7 precompute: emit a complete snapshot dir (feature store + keys + manifest)"
```

---

### Task 3: `PlannerStore.from_snapshot_dir` — boot with no extract

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py` (add one classmethod after `from_snapshot`, ~line 227, + one import)
- Test: `services/agent-spine/tests/bff/test_precompute.py` (append four tests)

**Interfaces:**
- Consumes: `load_store` (Task 1); the snapshot dir layout (Task 2); existing `PlannerStore._build`, `_read_manifest`, `Recommendation.model_validate`, `TenantContext`.
- Produces: `PlannerStore.from_snapshot_dir(*, tenant_id: str, snapshot_dir: str, writeback: InMemoryWritebackTarget | None = None) -> PlannerStore` — Task 4's `asgi.py` calls exactly this.

- [ ] **Step 1: Write the failing tests**

Append to `services/agent-spine/tests/bff/test_precompute.py` (add `import pytest` to the file's imports):

```python
def test_from_snapshot_dir_matches_from_extract(tmp_path):
    out_dir, _meta = _precompute(tmp_path)

    snap = PlannerStore.from_snapshot_dir(tenant_id="acme", snapshot_dir=str(out_dir))
    ext = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=_NOW, pool_by_part=False
    )

    def _key(rows):
        return [
            (r.pn, r.location, r.type, r.recommended_quantity, r.status, r.tier)
            for r in rows
        ]

    for status in (TaskStatus.PENDING, TaskStatus.APPROVED, TaskStatus.REJECTED):
        assert _key(snap.queue(status=status, limit=10_000)) == _key(
            ext.queue(status=status, limit=10_000)
        )

    # The snapshot's feature store serves the same reads as the extract-built one.
    assert sorted(snap.keys) == sorted(ext.keys)
    assert snap.dashboard().model_dump() == ext.dashboard().model_dump()
    pn, location = sorted(snap.keys)[0]
    assert snap.part_context(pn, location).model_dump() == ext.part_context(
        pn, location
    ).model_dump()
    # Manifest travels inside the snapshot dir (feeds view input).
    assert snap._manifest == ext._manifest
    assert snap._manifest != {}


def test_from_snapshot_dir_ids_are_loaded_verbatim(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    ids_on_disk = {
        r["recommendation_id"] for r in json.loads((out_dir / "recs.json").read_text())
    }
    snap = PlannerStore.from_snapshot_dir(tenant_id="acme", snapshot_dir=str(out_dir))
    ids_in_store = set()
    for status in (TaskStatus.PENDING, TaskStatus.APPROVED, TaskStatus.REJECTED):
        ids_in_store |= {r.recommendation_id for r in snap.queue(status=status, limit=10_000)}
    assert ids_in_store <= ids_on_disk


def test_from_snapshot_dir_tenant_mismatch_fails(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    with pytest.raises(ValueError, match="tenant"):
        PlannerStore.from_snapshot_dir(tenant_id="globex", snapshot_dir=str(out_dir))


def test_from_snapshot_dir_missing_artifact_fails(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    (out_dir / "keys.json").unlink()
    with pytest.raises(FileNotFoundError, match="keys.json"):
        PlannerStore.from_snapshot_dir(tenant_id="acme", snapshot_dir=str(out_dir))
```

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run --no-sync --extra bff pytest tests/bff/test_precompute.py -v -k from_snapshot_dir
```

Expected: 4 FAILED — `AttributeError: ... has no attribute 'from_snapshot_dir'`.

- [ ] **Step 3: Implement**

In `services/agent-spine/src/trax_io_spine/bff/store.py`, add to the imports (next to `from trax_io_feature_store import TenantContext`):

```python
from trax_io_feature_store.snapshot import load_store
```

Insert after the `from_snapshot` classmethod (before `_build`):

```python
    @classmethod
    def from_snapshot_dir(
        cls, *, tenant_id: str, snapshot_dir: str,
        writeback: InMemoryWritebackTarget | None = None,
    ) -> PlannerStore:
        """Fastest boot path: load the COMPLETE snapshot dir written by
        `bff/precompute.py` — the built (pooled) feature store, keys universe,
        manifest, and recommendations. No extract parsing, no pooling, and no
        engine run at boot; the extract dir is not needed at all (spec:
        docs/superpowers/specs/2026-07-02-fast-boot-feature-store-snapshot-design.md).

        Fail-loud by design: a missing artifact, a tenant mismatch, or feature-model
        schema drift (snapshot written by an older package version) raises rather
        than silently falling back to the slow path — unset PLANNER_SNAPSHOT_DIR to
        boot the old way.
        """
        sd = Path(snapshot_dir)
        for artifact in ("meta.json", "feature_store.json", "keys.json", "recs.json"):
            if not (sd / artifact).exists():
                raise FileNotFoundError(f"snapshot dir {snapshot_dir} is missing {artifact}")
        meta = json.loads((sd / "meta.json").read_text())
        if meta.get("tenant") != tenant_id:
            raise ValueError(
                f"snapshot tenant {meta.get('tenant')!r} does not match "
                f"requested tenant {tenant_id!r}"
            )
        fs = load_store(sd / "feature_store.json")
        keys = [tuple(k) for k in json.loads((sd / "keys.json").read_text())]
        recommendations = [
            Recommendation.model_validate(obj)
            for obj in json.loads((sd / "recs.json").read_text())
        ]
        return cls._build(
            fs=fs, tenant=TenantContext(tenant_id=tenant_id), keys=keys,
            recommendations=recommendations, writeback=writeback,
            manifest=_read_manifest(str(sd)),  # tolerant: absent manifest -> {} (feeds degrade)
        )
```

- [ ] **Step 4: Run the whole BFF + agent-spine suite and lint**

```bash
uv run --no-sync --extra bff pytest
uv run --no-sync --extra dev --extra bff ruff check .
```

Expected: all pass (was 136 + Wave-3/F-slice additions; +5 from Tasks 2–3), ruff clean.

- [ ] **Step 5: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/agent-spine/src/trax_io_spine/bff/store.py services/agent-spine/tests/bff/test_precompute.py
git commit -m "#7 BFF: PlannerStore.from_snapshot_dir — boot from the complete snapshot, no extract"
```

---

### Task 4: `asgi.py` — `PLANNER_SNAPSHOT_DIR` env wiring

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/asgi.py` (full rewrite below — the module is small)
- Test: Create `services/agent-spine/tests/bff/test_asgi.py`

**Interfaces:**
- Consumes: `PlannerStore.from_snapshot_dir` (Task 3); precompute `run` (Task 2, for the test fixture).
- Produces: env contract `PLANNER_SNAPSHOT_DIR` > `PLANNER_RECS_FILE` > `from_extract` default. Env reads move INSIDE `build_app()` so tests can exercise the precedence without reimport tricks (the module-level `app = build_app()` still runs at import for uvicorn).

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/bff/test_asgi.py`:

```python
"""PLANNER_SNAPSHOT_DIR boot path: build_app seeds via from_snapshot_dir.

The asgi module builds a module-level `app` at import (uvicorn entrypoint), so the
test sets ALL relevant env BEFORE importing it; `build_app()` re-reads env per call.
The ids-verbatim assertion is the fast-path proof: a `from_extract` fallback would
mint fresh ULIDs, so serving the on-disk ids means the snapshot actually seeded it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from trax_io_spine.bff.precompute import run as run_precompute

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def test_build_app_prefers_snapshot_dir(tmp_path, monkeypatch):
    out_dir = tmp_path / "snapshot"
    meta = run_precompute(
        argparse.Namespace(
            extract_dir=str(_SAMPLE), tenant="acme", now="2026-04-01T00:00:00+00:00",
            out=str(out_dir), pool_by_part=False, projector="historical",
        )
    )

    monkeypatch.setenv("PLANNER_SNAPSHOT_DIR", str(out_dir))
    monkeypatch.setenv("PLANNER_TENANT", "acme")
    # Keeps the module-level default app importable regardless of test CWD.
    monkeypatch.setenv("EXTRACT_DIR", str(_SAMPLE))

    from trax_io_spine.bff.asgi import build_app

    client = TestClient(build_app())
    assert client.get("/v1/tenants/acme/killswitch").status_code == 200
    body = client.get("/v1/tenants/acme/recommendations").json()
    assert body["total"] >= 1
    assert body["total"] <= meta["count"]

    # Fast path proof: the served ids must be the precomputed ones, verbatim
    # (a from_extract fallback would mint fresh ULIDs and this set check fails).
    ids_on_disk = {
        r["recommendation_id"]
        for r in json.loads((out_dir / "recs.json").read_text())
    }
    served_ids = {item["recommendation_id"] for item in body["items"]}
    assert served_ids <= ids_on_disk
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/agent-spine"
uv run --no-sync --extra bff pytest tests/bff/test_asgi.py -v
```

Expected: FAIL. The current asgi ignores `PLANNER_SNAPSHOT_DIR` and (with no `PLANNER_RECS_FILE`) boots via `from_extract`, which mints fresh ULIDs — so the run fails on the `served_ids <= ids_on_disk` assertion (or errors at import if the module can't build its default app — either way red).

- [ ] **Step 3: Rewrite `asgi.py`**

Replace the entire file content with:

```python
"""ASGI entrypoint for deploying the Planner-UI BFF.

Seeds an in-memory `PlannerStore` for one tenant and exposes the FastAPI app for
uvicorn. Deploy-only — keeps `create_planner_app` pure. Config via env:
  PLANNER_TENANT       tenant id to seed     (default: acme)
  PLANNER_SNAPSHOT_DIR path to a COMPLETE precomputed snapshot dir (feature store +
                       keys + manifest + recs — see bff/precompute.py). When set,
                       seeds via `PlannerStore.from_snapshot_dir`: no extract parsing,
                       no pooling, no engine at boot. Takes precedence over the two
                       paths below; the extract dir is not needed at all.
  PLANNER_RECS_FILE    path to a precomputed recs.json only. When set (and no
                       PLANNER_SNAPSHOT_DIR), seeds via `PlannerStore.from_snapshot`:
                       skips the engine but still rebuilds the feature store from
                       EXTRACT_DIR.
  EXTRACT_DIR          path to the extract dir (default: examples/extract_sample,
                       relative to CWD)
  PLANNER_NOW          ISO 'now' for the run  (default: 2026-04-01T00:00:00+00:00)
  PLANNER_POOL_BY_PART truthy for real eMRO extracts (network-pooled on-hand/demand)
  PLANNER_PROJECTOR    "statistical" or "historical" (default) — from_extract only

Env is read inside `build_app()` (not at module import) so tests can exercise the
precedence; the module-level `app = build_app()` below is the uvicorn entrypoint.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.store import PlannerStore


def build_app():
    tenant = os.environ.get("PLANNER_TENANT", "acme")
    snapshot_dir = os.environ.get("PLANNER_SNAPSHOT_DIR", "").strip() or None
    recs_file = os.environ.get("PLANNER_RECS_FILE", "").strip() or None
    extract_dir = os.environ.get("EXTRACT_DIR", "examples/extract_sample")
    now = datetime.fromisoformat(
        os.environ.get("PLANNER_NOW", "2026-04-01T00:00:00+00:00")
    ).astimezone(UTC)
    pool_by_part = (
        os.environ.get("PLANNER_POOL_BY_PART", "").strip().lower() in {"1", "true", "yes"}
    )
    use_statistical = (
        os.environ.get("PLANNER_PROJECTOR", "historical").strip().lower() == "statistical"
    )

    if snapshot_dir:
        store = PlannerStore.from_snapshot_dir(tenant_id=tenant, snapshot_dir=snapshot_dir)
    elif recs_file:
        store = PlannerStore.from_snapshot(
            tenant_id=tenant,
            extract_dir=extract_dir,
            recs_file=recs_file,
            now=now,
            pool_by_part=pool_by_part,
        )
    else:
        store = PlannerStore.from_extract(
            tenant_id=tenant,
            extract_dir=extract_dir,
            now=now,
            pool_by_part=pool_by_part,
            use_statistical=use_statistical,
        )
    return create_planner_app({tenant: store})


app = build_app()
```

- [ ] **Step 4: Run the test to verify it passes, then the full suite + lint**

```bash
uv run --no-sync --extra bff pytest tests/bff/test_asgi.py -v
uv run --no-sync --extra bff pytest
uv run --no-sync --extra dev --extra bff ruff check .
```

Expected: test_asgi 1 passed; full suite green; ruff clean.

- [ ] **Step 5: Commit**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
git add services/agent-spine/src/trax_io_spine/bff/asgi.py services/agent-spine/tests/bff/test_asgi.py
git commit -m "#7 asgi: PLANNER_SNAPSHOT_DIR fast-boot path (env read inside build_app)"
```

---

### Task 5: OPS — regenerate the real snapshot, flip compose, redeploy, measure (controller-run, not a subagent task)

**Files:**
- Modify: `docker-compose.yml`, `ROADMAP.md`, `TASKS.md`, `CLAUDE.md`
- Data (gitignored): `deploy/_local_extract/emro_net_snapshot/`

- [ ] **Step 1: Regenerate the snapshot from the real 22.9K extract** (offline, ~3–5 min — the engine reruns)

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf/services/agent-spine"
uv run --extra bff trax-io-precompute \
  --extract-dir ../../deploy/_local_extract/emro_net \
  --tenant acme --now 2024-04-01T00:00:00+00:00 \
  --pool-by-part --projector statistical \
  --out ../../deploy/_local_extract/emro_net_snapshot
du -sh ../../deploy/_local_extract/emro_net_snapshot/*
```

Record: rec count (expect ~12K), keys (expect 22,899), unique values vs entries (the interning ratio), file sizes (feature_store.json expected well under the 282MB extract), elapsed.

- [ ] **Step 2: Flip `docker-compose.yml`'s bff service to the snapshot**

Replace the bff service's `volumes:`, `environment:`, and the trailing healthcheck comment with:

```yaml
    # Boot from a COMPLETE precomputed snapshot (trax-io-precompute --out): the built
    # (pooled) feature store + keys + manifest + recs load via from_snapshot_dir — no
    # extract parsing, no pooling, no engine at boot. Gitignored local real data,
    # mounted read-only. To revert to the slower paths: unset PLANNER_SNAPSHOT_DIR and
    # restore EXTRACT_DIR (+ optional PLANNER_RECS_FILE) — see bff/asgi.py.
    volumes:
      - ./deploy/_local_extract/emro_net_snapshot:/data/snapshot:ro
    environment:
      - PLANNER_SNAPSHOT_DIR=/data/snapshot
      - PLANNER_TENANT=acme
```

and set the healthcheck `start_period` to `60s` provisionally (tightened after Step 4's measurement):

```yaml
      start_period: 60s # from_snapshot_dir loads the precomputed feature store — measured boot recorded in TASKS.md
```

- [ ] **Step 3: Rebuild + redeploy (project-scoped, single sequential build)**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf"
docker compose build bff
docker compose up -d
```

Never touch the `oracle`/`oracle19c`/MySQL containers.

- [ ] **Step 4: Measure boot + verify live**

```bash
docker logs trax-io-bff 2>&1 | head -20        # uvicorn startup lines
docker inspect -f '{{json .State.Health.Status}}' trax-io-bff
curl -s http://localhost:8001/v1/tenants/acme/killswitch
curl -s http://localhost:8001/v1/tenants/acme/dashboard | head -c 300
```

Measure boot as (first healthy healthcheck − container start) from `docker inspect -f '{{json .State.Health}}' trax-io-bff` timestamps. Expected: seconds to low tens (vs ~190s before). Then spot-check http://localhost:8089 (web) renders the real portfolio; the web app's Data & Connections page shows feed statuses (proves the manifest copy). Adjust `start_period` to ~2× the measured boot (minimum 30s) and `docker compose up -d` once more if it changed.

If boot is NOT dramatically faster, stop and investigate before proceeding (the spec's risk section names `json.load` dominance as the suspect) — do not ship compression ad hoc.

- [ ] **Step 5: Update trackers + commit + push**

- `ROADMAP.md`: under sub-project #7, add a dated bullet: fast-boot feature-store snapshot (interned JSON `dump_store`/`load_store`, complete `trax-io-precompute` snapshot dir, `from_snapshot_dir` + `PLANNER_SNAPSHOT_DIR`, measured boot Xs vs ~190s).
- `TASKS.md`: new session entry with what shipped, the measured numbers (boot before/after, snapshot sizes, interning ratio), and test counts.
- `CLAUDE.md` Section A: update the Docker deploy bullet (BFF now boots from `PLANNER_SNAPSHOT_DIR` snapshot; extract mount gone) and the BFF bullet's `EXTRACT_DIR` mention (now: snapshot dir preferred).
- Commit + push (replace `<X>` with the boot seconds actually measured in Step 4):

```bash
git add docker-compose.yml ROADMAP.md TASKS.md CLAUDE.md
git commit -m "#7 deploy: boot BFF from the complete feature-store snapshot (measured <X>s, was ~190s)"
git push
```

---

## Done when

The deployed BFF boots from `PLANNER_SNAPSHOT_DIR` in seconds (measured + recorded), serves the same 22.9K-key portfolio (dashboard/queue/parts/feeds verified live), the extract mount is gone from compose, all four suites are green (feature-store, agent-spine BFF, reco, both UIs untouched), and trackers are updated + pushed.
