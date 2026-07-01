"""ASGI entrypoint for deploying the Planner-UI BFF.

Seeds an in-memory `PlannerStore` for one tenant from a nightly-extract directory (the
sample extract by default) and exposes the FastAPI app for uvicorn. Deploy-only — keeps
`create_planner_app` pure. Config via env:
  PLANNER_TENANT   tenant id to seed       (default: acme)
  EXTRACT_DIR      path to the extract dir (default: examples/extract_sample, relative to CWD)
  PLANNER_NOW      ISO 'now' for the run    (default: 2026-04-01T00:00:00+00:00 — sample's epoch)
  PLANNER_PROJECTOR  demand projector: "statistical" (#5 StatisticalProjector, Croston/SBA/TSB
                     for the intermittent regime) or "historical" (default: deterministic
                     HistoricalScheduledProjector)
  PLANNER_RECS_FILE  path to a precomputed recs.json (see bff/precompute.py). When set, seeds
                     via `PlannerStore.from_snapshot` (fast — no RecommendationService.run at
                     boot) instead of `from_extract`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.store import PlannerStore

_TENANT = os.environ.get("PLANNER_TENANT", "acme")
_EXTRACT_DIR = os.environ.get("EXTRACT_DIR", "examples/extract_sample")
_NOW = datetime.fromisoformat(
    os.environ.get("PLANNER_NOW", "2026-04-01T00:00:00+00:00")
).astimezone(UTC)
# PLANNER_POOL_BY_PART: set truthy for real eMRO extracts (network-pooled on-hand/demand).
_POOL_BY_PART = os.environ.get("PLANNER_POOL_BY_PART", "").strip().lower() in {"1", "true", "yes"}
_USE_STATISTICAL = (
    os.environ.get("PLANNER_PROJECTOR", "historical").strip().lower() == "statistical"
)
_RECS_FILE = os.environ.get("PLANNER_RECS_FILE", "").strip() or None


def build_app():
    if _RECS_FILE:
        store = PlannerStore.from_snapshot(
            tenant_id=_TENANT,
            extract_dir=_EXTRACT_DIR,
            recs_file=_RECS_FILE,
            now=_NOW,
            pool_by_part=_POOL_BY_PART,
        )
    else:
        store = PlannerStore.from_extract(
            tenant_id=_TENANT,
            extract_dir=_EXTRACT_DIR,
            now=_NOW,
            pool_by_part=_POOL_BY_PART,
            use_statistical=_USE_STATISTICAL,
        )
    return create_planner_app({_TENANT: store})


app = build_app()
