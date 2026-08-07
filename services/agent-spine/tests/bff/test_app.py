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


def test_queue_endpoint_pagination_pages_and_totals(seed_pending_recommendations):
    client, store = _client()
    seed_pending_recommendations(store)
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


def test_healthz_reports_static_tenants_when_no_registry():
    # C5 Task 6: healthz's shape changes ONLY when a registry is configured
    # (see tests/pg/test_c5_multi_tenant_serving.py for that path — it must
    # not disclose slugs). Every dev/in-memory boot path (this one — no
    # `registry=` passed) is unchanged: `stores` IS the deployment's entire
    # configured tenant set, not a cache, so naming it here was never a
    # per-caller information disclosure.
    client, _ = _client()
    assert client.get("/healthz").json() == {"ok": True, "tenants": ["acme"]}


def test_detail_unknown_rec_404():
    client, _ = _client()
    assert client.get("/v1/tenants/acme/recommendations/nope").status_code == 404


def test_approve_then_history(seed_pending_recommendations):
    client, store = _client()
    seed_pending_recommendations(store, count=1)
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


def test_killswitch_blocks_approve_with_423(seed_pending_recommendations):
    client, store = _client()
    seed_pending_recommendations(store, count=1)
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
    acme_part = acme_body["items"][0]
    assert client.get(
        f"/v1/tenants/globex/parts/{acme_part['pn']}/{acme_part['location']}"
    ).status_code == 404
    # acme is unaffected — still sees its own recommendation
    assert client.get(f"/v1/tenants/acme/recommendations/{acme_rid}").status_code == 200


def test_get_part_context():
    client, store = _client()
    pn, loc = "HYD-PUMP-001", "YYZ"
    r = client.get(f"/v1/tenants/acme/parts/{pn}/{loc}")
    assert r.status_code == 200
    body = r.json()
    assert body["pn"] == pn
    assert body["attributes"]["description"]
    supply_cycle_fields = {
        "condition",
        "status",
        "mean_days",
        "p50_days",
        "p90_days",
        "p99_days",
        "n_observations",
        "source",
        "grouping_level",
        "confidence",
        "data_cutoff",
        "model_version",
        "classification_source",
        "proxy_definition",
        "proxy_label",
        "unavailable_reason",
    }
    assert set(body["procurement_lead_time"]) == supply_cycle_fields
    assert body["procurement_lead_time"]["condition"] == "NEW"
    assert set(body["repair_cycle_time"]) == supply_cycle_fields
    assert body["repair_cycle_time"]["condition"] == "REP"
    repair_pipeline = body["repair_pipeline"]
    assert repair_pipeline is not None
    assert set(repair_pipeline) == {
        "contract_version",
        "tenant_id",
        "part_number",
        "location_code",
        "as_of",
        "status",
        "aggregate_wip_quantity",
        "identified_open_quantity",
        "unidentified_source_quantity",
        "eligible_quantity",
        "excluded_identifiable_quantity",
        "aggregate_residual_quantity",
        "source_overflow_quantity",
        "time_phased_credit_quantity",
        "included",
        "exclusions",
        "warning_codes",
        "evidence_source",
    }
    assert repair_pipeline["tenant_id"] == "acme"
    assert repair_pipeline["part_number"] == pn
    assert repair_pipeline["location_code"] == loc
    assert repair_pipeline["time_phased_credit_quantity"] == 0
    assert repair_pipeline["eligible_quantity"] <= repair_pipeline["aggregate_wip_quantity"]
    repair_returns = body["repair_return_profile"]
    assert repair_returns is not None
    assert set(repair_returns) == {
        "contract_version",
        "tenant_id",
        "part_number",
        "location_code",
        "as_of",
        "status",
        "eligible_quantity",
        "excluded_quantity",
        "aggregate_residual_quantity",
        "horizons",
        "exclusions",
        "evidence",
        "warning_codes",
    }
    assert repair_returns["contract_version"] == "repair-return-profile.v1"
    assert repair_returns["tenant_id"] == "acme"
    assert repair_returns["part_number"] == pn
    assert repair_returns["location_code"] == loc
    assert [horizon["horizon_days"] for horizon in repair_returns["horizons"]] == [
        30,
        60,
        90,
    ]
    assert set(repair_returns["evidence"]) == {
        "method",
        "completed_observations",
        "right_censored_observations",
        "serviceable_yield",
        "tat_multiplier",
        "source",
        "confidence",
        "data_cutoff",
        "model_version",
        "proxy_definition",
    }
    # The existing wire field remains the NEW-only compatibility projection.
    assert body["lead_time"] == (
        store.part_context(pn, loc).lead_time.model_dump(mode="json")
        if store.part_context(pn, loc).lead_time is not None
        else None
    )
    trace = body["planning_trace"]
    assert set(trace) == {
        "calculation_source",
        "as_of",
        "horizon_end",
        "observation_start",
        "observation_end",
        "exposure_days",
        "bucket",
        "observed_periods",
        "zero_filled_periods",
        "demand_event_count",
        "event_count_source",
        "demanded_units",
        "historical_per_day",
        "horizon_days",
        "projection_kind",
        "served_historical_per_day",
        "projected_historical_demand",
        "scheduled_demand_status",
        "scheduled_demand_undated_lines",
        "scheduled_demand_undated_units",
        "scheduled_demand_due",
        "projected_demand",
        "dispatchable_available",
        "open_receipts_status",
        "open_receipts_undated_lines",
        "open_receipts_undated_units",
        "open_receipts_due",
        "overdue_open_receipts_due",
        "repair_receipts_due",
        "expected_receipts_due",
        "net_position",
        "shortage_before_action",
        "pooled_group_id",
        "pooling_scope",
        "excluded_member_keys",
        "members",
        "constraints",
        "warnings",
    }
    assert trace["event_count_source"] in {
        "observed",
        "bucket_fallback",
        "unavailable",
    }
    assert "tenant_id" not in trace
    frontier = body["candidate_frontier"]
    assert frontier is not None
    assert frontier == store.part_context(pn, loc).candidate_frontier.model_dump(
        mode="json"
    )
    assert frontier["tenant_id"] == "acme"
    assert sum(candidate["is_no_change"] for candidate in frontier["candidates"]) == 1


def test_get_part_context_unknown_404():
    client, _ = _client()
    assert client.get("/v1/tenants/acme/parts/NOPE/NOWHERE").status_code == 404


def test_get_part_context_uses_selected_recommendation_and_hides_mismatches():
    client, store = _client()
    key = ("HYD-PUMP-001", "YYZ")
    key_entries = [
        entry
        for entry in store._entries.values()
        if (entry.rec.part_number, entry.rec.current_location) == key
    ]
    selected = next(entry for entry in key_entries if entry.rec.policy is None)
    assert selected.rec.calculation_evidence is not None

    response = client.get(
        f"/v1/tenants/acme/parts/{key[0]}/{key[1]}",
        params={"recommendation_id": selected.rec.recommendation_id},
    )

    assert response.status_code == 200
    assert (
        response.json()["planning_trace"]["projected_demand"]
        == selected.rec.calculation_evidence.projected_demand
    )
    assert response.json()["candidate_frontier"] == store.part_context(
        *key
    ).candidate_frontier.model_dump(mode="json")

    other = next(
        entry
        for entry in store._entries.values()
        if (entry.rec.part_number, entry.rec.current_location) != key
    )
    for recommendation_id in ("unknown-rec", other.rec.recommendation_id):
        mismatch = client.get(
            f"/v1/tenants/acme/parts/{key[0]}/{key[1]}",
            params={"recommendation_id": recommendation_id},
        )
        assert mismatch.status_code == 404
        assert mismatch.json()["detail"] == f"{key[0]}/{key[1]}"


def test_get_dashboard():
    client, store = _client()
    r = client.get("/v1/tenants/acme/dashboard")
    assert r.status_code == 200
    assert r.json()["parts"] == len(store.keys)


# --------------------------------------------------------------------------- #
# Task F2 — server-side sort + filter on the queue endpoint
# --------------------------------------------------------------------------- #
def test_queue_bad_sort_by_422():
    client, _ = _client()
    assert client.get("/v1/tenants/acme/recommendations?sort_by=bogus").status_code == 422


def test_queue_bad_sort_dir_422():
    client, _ = _client()
    assert client.get("/v1/tenants/acme/recommendations?sort_dir=bogus").status_code == 422


def test_queue_composes_tier_type_aog_min_and_sort_by():
    client, _ = _client()
    full = client.get("/v1/tenants/acme/recommendations?limit=200").json()["items"]
    target = full[0]
    r = client.get(
        "/v1/tenants/acme/recommendations",
        params={
            "tier": target["tier"],
            "type": target["type"],
            "aog_min": 0,
            "sort_by": "estimated_cost_impact",
            "sort_dir": "asc",
            "limit": 200,
        },
    )
    assert r.status_code == 200
    body = r.json()
    rows = body["items"]
    assert rows
    assert all(row["tier"] == target["tier"] for row in rows)
    assert all(row["type"] == target["type"] for row in rows)
    costs = [float(row["estimated_cost_impact"]) for row in rows]
    assert costs == sorted(costs)
    assert body["total"] == len(rows)


def test_queue_no_new_params_response_identical_to_before():
    """With none of the new F2 query params, the response must be byte-identical to
    the pre-F2 shape/ordering (existing tests already cover this — this test pins the
    same invariant explicitly under the F2 param surface without touching them)."""
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
