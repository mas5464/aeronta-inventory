"""apps/web CSV export route — content, headers, filter narrowing, tenant 404."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.csv_export import CSV_COLUMNS
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)
_NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _client() -> TestClient:
    store = PlannerStore.from_extract(tenant_id="acme", extract_dir=str(_SAMPLE), now=_NOW)
    return TestClient(create_planner_app({"acme": store}))


def test_export_route_returns_csv_with_attachment_header():
    resp = _client().get("/v1/tenants/acme/recommendations/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == (
        'attachment; filename="trax-io-pending-recommendations.csv"'
    )


def test_export_body_has_14_column_header_and_one_row_per_pending_rec():
    client = _client()
    resp = client.get("/v1/tenants/acme/recommendations/export.csv")
    parsed = list(csv.reader(io.StringIO(resp.text)))
    assert parsed[0] == list(CSV_COLUMNS)
    # data rows == the full pending queue total the paged endpoint reports.
    total = client.get("/v1/tenants/acme/recommendations?limit=1&offset=0").json()["total"]
    assert len(parsed) - 1 == total


def test_export_narrows_with_tier_filter_like_the_paged_endpoint():
    client = _client()
    export = client.get("/v1/tenants/acme/recommendations/export.csv?tier=1")
    parsed = list(csv.reader(io.StringIO(export.text)))
    total_tier1 = client.get(
        "/v1/tenants/acme/recommendations?tier=1&limit=1&offset=0"
    ).json()["total"]
    assert len(parsed) - 1 == total_tier1
    tier_col = list(CSV_COLUMNS).index("tier")
    assert all(row[tier_col] == "1" for row in parsed[1:])


def test_export_filename_reflects_status():
    resp = _client().get("/v1/tenants/acme/recommendations/export.csv?status=approved")
    assert resp.headers["content-disposition"] == (
        'attachment; filename="trax-io-approved-recommendations.csv"'
    )


def test_export_unknown_tenant_404():
    resp = _client().get("/v1/tenants/ghost/recommendations/export.csv")
    assert resp.status_code == 404


def test_export_csv_path_is_not_shadowed_by_the_detail_route():
    # Regression guard: export.csv must NOT be captured as rec_id="export.csv"
    # by the /recommendations/{rec_id} detail route. A 404 here would mean the
    # export route is declared AFTER the detail route (wrong order).
    resp = _client().get("/v1/tenants/acme/recommendations/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
