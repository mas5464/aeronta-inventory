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
