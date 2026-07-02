"""#8 BVR: BFF reports surface — JSON/HTML/PDF routes, memoization + invalidation,
tenant isolation. Seeded from the committed sample extract like test_precompute."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.models import TaskStatus
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)
_NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _store() -> PlannerStore:
    return PlannerStore.from_extract(tenant_id="acme", extract_dir=str(_SAMPLE), now=_NOW)


def test_bvr_json_shape_and_projected_labeling():
    store = _store()
    client = TestClient(create_planner_app({"acme": store}))
    body = client.get("/v1/tenants/acme/reports/bvr").json()
    assert body["schema_version"] == "1.0.0"
    assert body["tenant_id"] == "acme"
    assert body["savings"]["changes_total"] == body["governance"]["writes_written"] + (
        body["governance"]["writes_shadowed"]
    )
    # keys under management = the KeyStats-derivable subset (57,605 of 58,899 at network
    # scale), == store.keys only on the complete sample extract.
    assert body["executive_summary"]["keys_under_management"] == len(store._key_stats())
    assert body["service_posture"]["note"].startswith("Posture")


def test_bvr_html_route_serves_printable_document():
    client = TestClient(create_planner_app({"acme": _store()}))
    resp = client.get("/v1/tenants/acme/reports/bvr.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Business Value Report" in resp.text
    assert "ALL FIGURES PROJECTED" in resp.text


def test_bvr_memoized_and_invalidated_by_approve():
    store = _store()
    first = store.bvr()
    assert store.bvr() is first  # memoized
    pending = store.queue(status=TaskStatus.PENDING, limit=10)
    approvable = next(r for r in pending if r.approvable)
    store.approve(approvable.recommendation_id)
    second = store.bvr()
    assert second is not first
    assert second.governance.approved == first.governance.approved + 1
    assert second.governance.writes_written >= first.governance.writes_written


def test_bvr_pdf_route_501_when_extra_absent(monkeypatch):
    # Simulate the pdf extra being unavailable regardless of this machine's env.
    import trax_io_spine.bff.app as app_mod
    from trax_io_spine.bvr.pdf import PdfUnavailable

    def _boom(html: str) -> bytes:
        raise PdfUnavailable("pdf extra not installed")

    monkeypatch.setattr(app_mod, "render_pdf", _boom)
    client = TestClient(create_planner_app({"acme": _store()}))
    resp = client.get("/v1/tenants/acme/reports/bvr.pdf")
    assert resp.status_code == 501
    assert "pdf" in resp.json()["detail"].lower()


def test_bvr_pdf_route_sets_download_header(monkeypatch):
    import trax_io_spine.bff.app as app_mod

    monkeypatch.setattr(app_mod, "render_pdf", lambda html: b"%PDF-fake")
    client = TestClient(create_planner_app({"acme": _store()}))
    resp = client.get("/v1/tenants/acme/reports/bvr.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="bvr-acme.pdf"'


def test_bvr_tenant_isolation():
    client = TestClient(create_planner_app({"acme": _store()}))
    assert client.get("/v1/tenants/globex/reports/bvr").status_code == 404


def test_bvr_reflects_kill_switch_toggle():
    store = _store()
    assert store.bvr().governance.kill_switch_engaged is False
    store.set_kill_switch(True)
    assert store.bvr().governance.kill_switch_engaged is True
