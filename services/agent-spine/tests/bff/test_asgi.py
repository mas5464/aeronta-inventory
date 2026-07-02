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
