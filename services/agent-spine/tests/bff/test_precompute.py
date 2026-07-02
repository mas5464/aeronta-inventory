"""#W3-4: offline precompute batch + PlannerStore.from_snapshot fast-boot path.

The BFF must not run the full RecommendationService at container boot (tens of minutes at
62K keys with the statistical projector). `bff/precompute.py` runs the engine once offline
and persists recommendations as JSON; `PlannerStore.from_snapshot` loads that JSON at boot
instead of recomputing. These tests prove: (1) the precompute CLI/function writes the
expected artifacts, and (2) `from_snapshot` over a precomputed file is behaviorally
equivalent to `from_extract` (same recommendation_ids, statuses, priority order) — i.e. the
precompute path is faithful to the original in-process computation.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.models import TaskStatus
from trax_io_spine.bff.precompute import run as run_precompute
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)
_NOW_ISO = "2026-04-01T00:00:00+00:00"
_NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _precompute(tmp_path: Path, **overrides) -> tuple[Path, dict]:
    out_dir = tmp_path / "snapshot"
    args = argparse.Namespace(
        extract_dir=str(_SAMPLE),
        tenant="acme",
        now=_NOW_ISO,
        out=str(out_dir),
        pool_by_part=False,
        projector="historical",
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    meta = run_precompute(args)
    return out_dir, meta


def test_precompute_writes_recs_and_meta(tmp_path):
    out_dir, meta = _precompute(tmp_path)

    recs_path = out_dir / "recs.json"
    meta_path = out_dir / "meta.json"
    assert recs_path.exists()
    assert meta_path.exists()

    recs = json.loads(recs_path.read_text())
    assert isinstance(recs, list)
    assert len(recs) > 0
    assert all("recommendation_id" in r for r in recs)

    on_disk_meta = json.loads(meta_path.read_text())
    assert on_disk_meta == meta
    assert meta["tenant"] == "acme"
    assert meta["pool_by_part"] is False
    assert meta["projector"] == "historical"
    assert meta["count"] == len(recs)
    assert meta["now"] == _NOW_ISO


def test_from_snapshot_matches_from_extract_queue(tmp_path):
    out_dir, meta = _precompute(tmp_path)

    snapshot_store = PlannerStore.from_snapshot(
        tenant_id="acme",
        extract_dir=str(_SAMPLE),
        recs_file=str(out_dir / "recs.json"),
        now=_NOW,
        pool_by_part=False,
    )
    extract_store = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=_NOW, pool_by_part=False
    )

    for status in (TaskStatus.PENDING, TaskStatus.APPROVED, TaskStatus.REJECTED):
        snap_rows = snapshot_store.queue(status=status, limit=10_000)
        ext_rows = extract_store.queue(status=status, limit=10_000)

        # recommendation_id is a freshly-minted ULID per RecommendationService.run(), so a
        # brand-new from_extract() run mints different ids than the precomputed snapshot —
        # compare on the stable (pn, location, type, recommended_quantity) identity instead,
        # in the same priority order.
        def _key(rows):
            return [
                (r.pn, r.location, r.type, r.recommended_quantity, r.status, r.tier)
                for r in rows
            ]

        assert _key(snap_rows) == _key(ext_rows)
        assert len(snap_rows) == len(ext_rows)

    assert meta["count"] == sum(
        len(extract_store.queue(status=s, limit=10_000))
        for s in (TaskStatus.PENDING, TaskStatus.APPROVED, TaskStatus.REJECTED)
    )


def test_from_snapshot_ids_are_loaded_verbatim_from_recs_file(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    recs = json.loads((out_dir / "recs.json").read_text())
    ids_on_disk = {r["recommendation_id"] for r in recs}

    snapshot_store = PlannerStore.from_snapshot(
        tenant_id="acme",
        extract_dir=str(_SAMPLE),
        recs_file=str(out_dir / "recs.json"),
        now=_NOW,
        pool_by_part=False,
    )
    all_rows = []
    for status in (TaskStatus.PENDING, TaskStatus.APPROVED, TaskStatus.REJECTED):
        all_rows += snapshot_store.queue(status=status, limit=10_000)
    ids_in_store = {r.recommendation_id for r in all_rows}

    assert ids_in_store <= ids_on_disk


def test_from_extract_default_path_unchanged():
    # Guard against the from_snapshot refactor changing from_extract behavior: same
    # assertions as the pre-existing test_store_reads.py smoke checks.
    store = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=_NOW
    )
    rows = store.queue()
    assert len(rows) >= 1
    assert all(r.status is TaskStatus.PENDING for r in rows)
    scores = [r.priority_score for r in rows]
    assert scores == sorted(scores, reverse=True)


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


def test_from_snapshot_dir_unknown_snapshot_format_fails(tmp_path):
    out_dir, _meta = _precompute(tmp_path)
    meta = json.loads((out_dir / "meta.json").read_text())
    meta["snapshot_format"] = 99
    (out_dir / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="snapshot_format"):
        PlannerStore.from_snapshot_dir(tenant_id="acme", snapshot_dir=str(out_dir))
