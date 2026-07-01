"""ASGI entrypoint for deploying the Planner-UI BFF.

Seeds an in-memory `PlannerStore` for one tenant from a nightly-extract directory (the
sample extract by default) and exposes the FastAPI app for uvicorn. Deploy-only — keeps
`create_planner_app` pure. Config via env:
  PLANNER_TENANT  tenant id to seed       (default: acme)
  EXTRACT_DIR     path to the extract dir (default: examples/extract_sample, relative to CWD)
  PLANNER_NOW     ISO 'now' for the run    (default: 2026-04-01T00:00:00+00:00 — the sample's epoch)
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


def build_app():
    store = PlannerStore.from_extract(tenant_id=_TENANT, extract_dir=_EXTRACT_DIR, now=_NOW)
    return create_planner_app({_TENANT: store})


app = build_app()
