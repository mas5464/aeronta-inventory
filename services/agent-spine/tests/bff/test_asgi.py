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

import pytest
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

    # Ambient-env guard: DATABASE_URL is highest precedence in build_app(), so any
    # machine/CI runner with it set (e.g. a shell exported for another project)
    # would otherwise divert this test into the pg boot branch.
    monkeypatch.delenv("DATABASE_URL", raising=False)
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


def test_build_app_database_url_fails_closed_without_verifier(monkeypatch):
    # Fail-closed guard: DATABASE_URL with no verifier configured must refuse to
    # boot BEFORE any DB connection is attempted. Monkeypatching make_pool to
    # explode proves the guard runs first — if the guard were missing (or ran
    # after make_pool), this test would fail on the AssertionError instead of
    # asserting the intended RuntimeError.
    import trax_io_spine.pg.db as pg_db

    def _make_pool_should_not_be_called(*args, **kwargs):
        raise AssertionError("should not be called")

    monkeypatch.setattr(pg_db, "make_pool", _make_pool_should_not_be_called)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@example.invalid/db")
    monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)

    from trax_io_spine.bff.asgi import build_app

    with pytest.raises(RuntimeError, match="AUTH_DEV_MODE"):
        build_app()


def test_build_app_native_features_fail_closed_without_verifier(monkeypatch):
    # Import the module-level uvicorn app with native mode disabled, then
    # enable it for the explicit build under test. Authentication must be
    # rejected before optional AWS clients are imported or any feature read
    # can occur.
    monkeypatch.delenv("TRAX_IO_FEATURE_ONLINE_TABLE", raising=False)
    monkeypatch.setenv("EXTRACT_DIR", str(_SAMPLE))
    from trax_io_spine.bff.asgi import build_app

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    monkeypatch.setenv("TRAX_IO_FEATURE_ONLINE_TABLE", "native-features")

    with pytest.raises(RuntimeError, match="refusing to serve native tenant data"):
        build_app()
