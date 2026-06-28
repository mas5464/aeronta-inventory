from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _client():
    store = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    return TestClient(create_planner_app({"acme": store})), store


def _policy_rec_id(client):
    for row in client.get("/v1/tenants/acme/recommendations").json():
        d = client.get(f"/v1/tenants/acme/recommendations/{row['recommendation_id']}").json()
        if d["proposed_policy"] is not None:
            return row["recommendation_id"]
    raise AssertionError("no policy-bearing rec")


def test_queue_endpoint_priority_desc():
    client, _ = _client()
    rows = client.get("/v1/tenants/acme/recommendations").json()
    assert len(rows) >= 1
    assert [r["priority_score"] for r in rows] == sorted(
        [r["priority_score"] for r in rows], reverse=True
    )


def test_unknown_tenant_404():
    client, _ = _client()
    assert client.get("/v1/tenants/ghost/recommendations").status_code == 404


def test_detail_unknown_rec_404():
    client, _ = _client()
    assert client.get("/v1/tenants/acme/recommendations/nope").status_code == 404


def test_approve_then_history():
    client, _ = _client()
    rid = _policy_rec_id(client)
    assert client.post(f"/v1/tenants/acme/recommendations/{rid}/approve").status_code == 200
    d = client.get(f"/v1/tenants/acme/recommendations/{rid}").json()
    hist = client.get(
        f"/v1/tenants/acme/history?pn={d['pn']}&location={d['location']}"
    ).json()
    assert len(hist) >= 1


def test_reject_body_and_status():
    client, _ = _client()
    rid = client.get("/v1/tenants/acme/recommendations").json()[0]["recommendation_id"]
    r = client.post(
        f"/v1/tenants/acme/recommendations/{rid}/reject",
        json={"reason": "wrong_for_fleet", "detail": "x"},
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_killswitch_blocks_approve_with_423():
    client, _ = _client()
    rid = _policy_rec_id(client)
    assert client.post("/v1/tenants/acme/killswitch", json={"engaged": True}).status_code == 200
    assert client.post(f"/v1/tenants/acme/recommendations/{rid}/approve").status_code == 423


def test_bad_reject_reason_422():
    client, _ = _client()
    rid = client.get("/v1/tenants/acme/recommendations").json()[0]["recommendation_id"]
    r = client.post(
        f"/v1/tenants/acme/recommendations/{rid}/reject", json={"reason": "not_a_reason"}
    )
    assert r.status_code == 422
