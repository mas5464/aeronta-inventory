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
    for row in client.get("/v1/tenants/acme/recommendations").json()["items"]:
        d = client.get(f"/v1/tenants/acme/recommendations/{row['recommendation_id']}").json()
        if d["proposed_policy"] is not None:
            return row["recommendation_id"]
    raise AssertionError("no policy-bearing rec")


def test_queue_endpoint_priority_desc():
    client, _ = _client()
    body = client.get("/v1/tenants/acme/recommendations").json()
    rows = body["items"]
    assert len(rows) >= 1
    assert [r["priority_score"] for r in rows] == sorted(
        [r["priority_score"] for r in rows], reverse=True
    )
    assert body["total"] >= len(rows)
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_queue_endpoint_pagination_pages_and_totals():
    client, _ = _client()
    page1 = client.get("/v1/tenants/acme/recommendations?limit=2&offset=0").json()
    assert len(page1["items"]) == 2
    assert page1["limit"] == 2 and page1["offset"] == 0

    full = client.get("/v1/tenants/acme/recommendations?limit=200").json()
    assert page1["total"] == full["total"] == len(full["items"])

    page2 = client.get("/v1/tenants/acme/recommendations?limit=2&offset=2").json()
    assert page2["offset"] == 2

    # sort is priority-desc and stable across pages: no dup, no skip.
    ids_via_pages = [r["recommendation_id"] for r in page1["items"] + page2["items"]]
    ids_via_full = [r["recommendation_id"] for r in full["items"][:4]]
    assert ids_via_pages == ids_via_full


def test_queue_endpoint_status_filter_with_paging():
    client, _ = _client()
    rid = client.get("/v1/tenants/acme/recommendations").json()["items"][0]["recommendation_id"]
    client.post(
        f"/v1/tenants/acme/recommendations/{rid}/reject", json={"reason": "other", "detail": ""}
    )
    rejected = client.get("/v1/tenants/acme/recommendations?status=rejected&limit=10").json()
    assert any(r["recommendation_id"] == rid for r in rejected["items"])
    assert rejected["total"] >= 1


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
    rid = client.get("/v1/tenants/acme/recommendations").json()["items"][0]["recommendation_id"]
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
    rid = client.get("/v1/tenants/acme/recommendations").json()["items"][0]["recommendation_id"]
    r = client.post(
        f"/v1/tenants/acme/recommendations/{rid}/reject", json={"reason": "not_a_reason"}
    )
    assert r.status_code == 422


def test_tenant_isolation():
    # spec §4: a second tenant's store is independent — A cannot see or act on B's recs.
    acme = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    globex = PlannerStore(tenant_id="globex")  # independent, empty store
    client = TestClient(create_planner_app({"acme": acme, "globex": globex}))

    acme_body = client.get("/v1/tenants/acme/recommendations").json()
    acme_rid = acme_body["items"][0]["recommendation_id"]
    # globex has its own (empty) queue and cannot see acme's recommendation
    globex_body = client.get("/v1/tenants/globex/recommendations").json()
    assert globex_body["items"] == [] and globex_body["total"] == 0
    assert client.get(f"/v1/tenants/globex/recommendations/{acme_rid}").status_code == 404
    # nor act on it
    assert client.post(f"/v1/tenants/globex/recommendations/{acme_rid}/approve").status_code == 404
    # acme is unaffected — still sees its own recommendation
    assert client.get(f"/v1/tenants/acme/recommendations/{acme_rid}").status_code == 200


def test_get_part_context():
    client, store = _client()
    pn, loc = store.keys[0]
    r = client.get(f"/v1/tenants/acme/parts/{pn}/{loc}")
    assert r.status_code == 200
    body = r.json()
    assert body["pn"] == pn
    assert body["attributes"]["description"]


def test_get_part_context_unknown_404():
    client, _ = _client()
    assert client.get("/v1/tenants/acme/parts/NOPE/NOWHERE").status_code == 404


def test_get_dashboard():
    client, store = _client()
    r = client.get("/v1/tenants/acme/dashboard")
    assert r.status_code == 200
    assert r.json()["parts"] == len(store.keys)
