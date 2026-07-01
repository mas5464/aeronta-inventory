"""Slice S6 — What-If Scenarios: solver unit tests + store/route round-trip."""

import math
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from trax_io_reco.contracts.context import DemandProjection, TenantPolicyConfig
from trax_io_reco.policy.R_Q import compute_R_Q
from trax_io_reco.policy.service_level import round_half_up

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.models import ScenarioParamsWire
from trax_io_spine.bff.scenario import (
    FRONTIER_SERVICE_LEVELS,
    KeyStats,
    ScenarioParams,
    ScenarioSolver,
    build_key_stats,
)
from trax_io_spine.bff.store import PlannerStore, ScenarioNotFound

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)
# tests/bff/test_scenario.py -> parents[3] == services/ -> parents[4] == repo root.
_LOCAL_EXTRACT_DIR = Path(__file__).resolve().parents[4] / "deploy" / "_local_extract"


def _store() -> PlannerStore:
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def _client():
    store = _store()
    return TestClient(create_planner_app({"acme": store})), store


def _make_key(
    *,
    pn="PN1",
    location="LOC1",
    criticality_tier=3,
    ata_chapter="32",
    mean_per_day=1.0,
    std_per_day=1.0,
    lead_mean=14.0,
    lead_var=4.0,
    unit_cost=100.0,
    min_order_qty=1,
    on_hand=50,
) -> KeyStats:
    return KeyStats(
        pn=pn,
        location=location,
        criticality_tier=criticality_tier,
        ata_chapter=ata_chapter,
        mean_per_day=mean_per_day,
        std_per_day=std_per_day,
        lead_mean=lead_mean,
        lead_var=lead_var,
        unit_cost=unit_cost,
        min_order_qty=min_order_qty,
        on_hand=on_hand,
    )


# --------------------------------------------------------------------------- #
# Solver unit tests — synthetic KeyStats (fast, deterministic, isolate the math)
# --------------------------------------------------------------------------- #


def test_solver_monotonicity_higher_sl_increases_investment_and_coverage():
    keys = [_make_key(pn=f"PN{i}", on_hand=20 + i) for i in range(25)]
    solver = ScenarioSolver(keys)

    prev_investment = None
    prev_coverage = None
    for sl in (0.80, 0.90, 0.95, 0.99, 0.999):
        result = solver.solve(ScenarioParams(service_level_target=sl))
        if prev_investment is not None:
            assert result.proposed.projected_investment >= prev_investment - 1e-6
            assert result.proposed.projected_coverage >= prev_coverage - 1e-9
        prev_investment = result.proposed.projected_investment
        prev_coverage = result.proposed.projected_coverage


def test_solver_frontier_is_monotonic_and_matches_configured_points():
    keys = [_make_key(pn=f"PN{i}") for i in range(10)]
    solver = ScenarioSolver(keys)
    result = solver.solve(ScenarioParams())

    assert [p.service_level for p in result.frontier] == list(FRONTIER_SERVICE_LEVELS)
    investments = [p.projected_investment for p in result.frontier]
    coverages = [p.projected_coverage for p in result.frontier]
    assert investments == sorted(investments)
    assert coverages == sorted(coverages)


def test_solver_lead_time_delta_increases_investment():
    keys = [_make_key(pn=f"PN{i}") for i in range(10)]
    solver = ScenarioSolver(keys)

    baseline = solver.solve(ScenarioParams(lead_time_delta_pct=0.0))
    longer_tat = solver.solve(ScenarioParams(lead_time_delta_pct=0.5))
    assert longer_tat.proposed.projected_investment > baseline.proposed.projected_investment


def test_solver_negative_lead_time_delta_clamped_at_zero():
    keys = [_make_key(pn="PN1", lead_mean=10.0)]
    solver = ScenarioSolver(keys)
    # -150% would go negative; solver must clamp lead_mean at 0, not error or go negative.
    result = solver.solve(ScenarioParams(lead_time_delta_pct=-1.5))
    assert result.proposed.projected_investment >= 0


def test_solver_budget_cap_binds_when_exceeded_and_not_when_generous():
    keys = [_make_key(pn=f"PN{i}", unit_cost=500.0) for i in range(20)]
    solver = ScenarioSolver(keys)

    tight = solver.solve(ScenarioParams(service_level_target=0.99, budget_cap=1.0))
    assert tight.budget_cap_binds is True

    generous = solver.solve(ScenarioParams(service_level_target=0.99, budget_cap=1e12))
    assert generous.budget_cap_binds is False

    no_cap = solver.solve(ScenarioParams(service_level_target=0.99, budget_cap=None))
    assert no_cap.budget_cap_binds is False


def test_solver_scope_all_scores_every_key():
    keys = [_make_key(pn=f"PN{i}", criticality_tier=(i % 5) + 1) for i in range(30)]
    solver = ScenarioSolver(keys)
    result = solver.solve(ScenarioParams(scope="all"))
    assert result.proposed.scored_keys == 30
    assert result.current.scored_keys == 30


def test_solver_scope_criticality_tier_filters_correctly():
    keys = [_make_key(pn=f"PN{i}", criticality_tier=(i % 5) + 1) for i in range(30)]
    solver = ScenarioSolver(keys)
    result = solver.solve(ScenarioParams(scope="criticality_tier", scope_value="1"))
    expected = sum(1 for i in range(30) if (i % 5) + 1 == 1)
    assert result.proposed.scored_keys == expected
    assert expected < 30  # sanity: scope actually narrowed the set


def test_solver_scope_ata_chapter_filters_correctly():
    keys = [
        _make_key(pn=f"PN{i}", ata_chapter="32" if i % 2 == 0 else "21") for i in range(10)
    ]
    solver = ScenarioSolver(keys)
    result = solver.solve(ScenarioParams(scope="ata_chapter", scope_value="32"))
    assert result.proposed.scored_keys == 5


def test_solver_scope_with_no_matches_scores_zero_not_error():
    keys = [_make_key(pn="PN1", criticality_tier=3)]
    solver = ScenarioSolver(keys)
    result = solver.solve(ScenarioParams(scope="criticality_tier", scope_value="999"))
    assert result.proposed.scored_keys == 0
    assert result.proposed.projected_investment == 0.0
    assert result.proposed.on_hand_gap_ratio == 1.0  # honest default for "nothing to fail"


def test_solver_skipped_keys_counts_globally_unscorable_not_scope_exclusions():
    keys = [_make_key(pn=f"PN{i}") for i in range(5)]
    # Simulate 3 keys in the tenant's real universe that build_key_stats couldn't score.
    solver = ScenarioSolver(keys, total_keys_in_universe=8)
    result = solver.solve(ScenarioParams())
    assert result.skipped_keys == 3
    assert result.total_keys == 8
    # Scoping to a tier that matches nothing must NOT inflate skipped_keys — that's a
    # scope-exclusion, a separate (and expected) concept from a data-quality gap.
    scoped_out = solver.solve(ScenarioParams(scope="criticality_tier", scope_value="999"))
    assert scoped_out.skipped_keys == 3
    assert scoped_out.proposed.scored_keys == 0


def test_solver_per_tier_service_level_overrides_take_precedence():
    # Isolate precedence on a single tier-1 key: a per-tier override for tier 1 must
    # win over both the global slider and the tenant-default target for that tier,
    # in both directions (override below AND above the global).
    keys = [_make_key(pn="tier1", criticality_tier=1)]
    solver = ScenarioSolver(keys)

    global_high = solver.solve(ScenarioParams(service_level_target=0.999))
    tier_override_low = solver.solve(
        ScenarioParams(service_level_target=0.999, service_level_by_tier={1: 0.80})
    )
    assert tier_override_low.proposed.service_level == pytest.approx(0.80)
    assert (
        tier_override_low.proposed.projected_investment
        < global_high.proposed.projected_investment
    )

    global_low = solver.solve(ScenarioParams(service_level_target=0.80))
    tier_override_high = solver.solve(
        ScenarioParams(service_level_target=0.80, service_level_by_tier={1: 0.999})
    )
    assert tier_override_high.proposed.service_level == pytest.approx(0.999)
    assert (
        tier_override_high.proposed.projected_investment > global_low.proposed.projected_investment
    )


def test_solver_matches_compute_r_q_including_review_period_protection():
    """Regression net for the missing periodic-review protection term (S6 review).

    `_solve_one` claims to mirror `compute_R_Q` (spec §6.2), which folds a fixed
    review-period into the protection period BEFORE computing LTD mean/variance
    (``protection = lead_mean + DEFAULT_REVIEW_PERIOD_DAYS``, used for both moments).
    Construct one synthetic key/projection pair with identical mean/std/lead/cost
    inputs and assert the solver's per-key ROP and safety stock — after the same
    round_half_up the engine applies — equal `compute_R_Q`'s output exactly. Without
    the review-period term this fails (rop/safety_stock come out lower).
    """
    mean_per_day = 2.0
    std_per_day = 1.5
    lead_mean = 20.0
    lead_var = 9.0
    unit_cost = 250.0
    min_order_qty = 5
    service_level = 0.95
    cfg = TenantPolicyConfig()
    # Tier chosen so the engine default service_level_by_tier[tier] == service_level
    # exactly (TenantPolicyConfig default: {1: .995, 2: .98, 3: .95, 4: .92, 5: .90}),
    # so both call paths solve for the identical target with no extra plumbing.
    tier = 3
    assert cfg.service_level_by_tier[tier] == service_level

    projection = DemandProjection(
        mean_per_day=mean_per_day,
        std_per_day=std_per_day,
        dist_kind="NORMAL",
        dist_params={},
        historical_component=mean_per_day,
        scheduled_component=0.0,
        basis_window_days=730,
    )
    engine_rop, engine_eoq, engine_ss, engine_max = compute_R_Q(
        projection=projection,
        lead_mean=lead_mean,
        lead_var=lead_var,
        service_level=service_level,
        ordering_cost=cfg.ordering_cost,
        holding_cost_rate=cfg.holding_cost_rate,
        unit_cost=unit_cost,
        min_order_qty=min_order_qty,
    )

    key = _make_key(
        pn="PARITY1",
        criticality_tier=tier,
        mean_per_day=mean_per_day,
        std_per_day=std_per_day,
        lead_mean=lead_mean,
        lead_var=lead_var,
        unit_cost=unit_cost,
        min_order_qty=min_order_qty,
        on_hand=0,
    )
    solver = ScenarioSolver([key])
    result = solver.solve(ScenarioParams())

    # `ScenarioOutcome` reports only network-rolled-up investment, not per-key
    # rop/safety_stock, so we can't read them back off the result object directly.
    # Instead, replicate `_solve_one`'s exact per-key expression (same variable names,
    # same operation order — copy of scenario.py lines computing protection/ltd_mean/
    # ltd_var/safety_stock/rop) independently here, apply the same round_half_up the
    # engine applies to its own output, and assert the *rounded* values are identical.
    # This is a faithful parity check, not a derived/back-solved approximation — it
    # fails whenever `_solve_one`'s formula diverges from `compute_R_Q`'s (e.g. if the
    # review-period term were dropped again, or applied to only one of mean/variance).
    from trax_io_reco.policy.R_Q import DEFAULT_REVIEW_PERIOD_DAYS
    from trax_io_reco.policy.service_level import z_for_fill_rate

    z = z_for_fill_rate(service_level)
    protection = lead_mean + DEFAULT_REVIEW_PERIOD_DAYS
    ltd_mean = mean_per_day * protection
    ltd_var = protection * (std_per_day**2) + (mean_per_day**2) * lead_var
    solver_safety_stock_raw = max(0.0, z * math.sqrt(ltd_var))
    solver_rop_raw = ltd_mean + solver_safety_stock_raw

    assert round_half_up(solver_safety_stock_raw) == engine_ss
    assert round_half_up(solver_rop_raw) == engine_rop
    assert engine_max == engine_rop + engine_eoq

    # eoq's Wilson-lot-size formula never sees lead time, so it's unaffected by this
    # fix. `compute_R_Q` returns it round_half_up'd for display; `_solve_one` keeps the
    # raw float (eoq_raw) for its own investment math (never rounds internally) — both
    # asserted against their respective counterparts below.
    annual_demand = mean_per_day * 365.0
    holding = cfg.holding_cost_rate * unit_cost
    eoq_raw = max(min_order_qty, math.sqrt(2.0 * annual_demand * cfg.ordering_cost / holding))
    assert max(min_order_qty, round_half_up(eoq_raw)) == engine_eoq

    # Sanity: the real solver path (through ScenarioSolver.solve, not the replicated
    # formula above) produces the exact investment `_solve_one` would compute from the
    # same raw (unrounded) rop/eoq — catches gross wiring errors (e.g. wrong key routed
    # in) independent of the rop/safety_stock/eoq assertions above.
    expected_investment = (solver_rop_raw + eoq_raw / 2.0) * unit_cost
    assert result.proposed.projected_investment == pytest.approx(expected_investment)


def test_build_key_stats_skips_keys_missing_feature_groups():
    class _FakeFs:
        def get_criticality(self, *, tenant, pn):
            if pn == "missing":
                raise KeyError("no criticality")
            return type("C", (), {"canonical_tier": 3})()

        def get_demand_history(self, *, tenant, pn, location):
            return type("DH", (), {"observations": []})()

        def get_vendor_economics(self, *, tenant, pn, vendor):
            return type("VE", (), {"unit_cost": 10.0, "minimum_order_qty": 1})()

        def get_stock_position(self, *, tenant, pn, location):
            return type("SP", (), {"on_hand": 5})()

        def get_lead_time_distribution(self, *, tenant, pn, vendor, condition):
            raise KeyError("no lead time")

        def get_part_attributes(self, *, tenant, pn):
            return None

    stats = build_key_stats(
        fs=_FakeFs(), tenant=None, keys=[("ok", "LOC1"), ("missing", "LOC1")]
    )
    assert len(stats) == 1
    assert stats[0].pn == "ok"
    assert stats[0].lead_mean == 14.0  # spec §6.5 fallback default


# --------------------------------------------------------------------------- #
# Store + route round-trip tests (sample extract, via TestClient)
# --------------------------------------------------------------------------- #


def test_store_solve_scenario_returns_result_over_real_sample():
    store = _store()
    result = store.solve_scenario(ScenarioParamsWire())
    assert result.total_keys == len(store.keys)
    assert result.proposed.scored_keys + result.skipped_keys <= result.total_keys
    assert result.current.projected_investment >= 0
    assert len(result.frontier) == len(FRONTIER_SERVICE_LEVELS)


def test_post_scenarios_solve_route():
    client, _ = _client()
    r = client.post("/v1/tenants/acme/scenarios/solve", json={"service_level_target": 0.97})
    assert r.status_code == 200
    body = r.json()
    assert "current" in body
    assert "proposed" in body
    assert "frontier" in body
    assert body["proposed"]["service_level"] == pytest.approx(0.97)


def test_post_scenarios_solve_route_defaults_body():
    client, _ = _client()
    r = client.post("/v1/tenants/acme/scenarios/solve", json={})
    assert r.status_code == 200


def test_solve_scenario_route_unknown_tenant_404():
    client, _ = _client()
    r = client.post("/v1/tenants/ghost/scenarios/solve", json={})
    assert r.status_code == 404


def test_save_list_get_delete_scenario_round_trip_via_route():
    client, _ = _client()
    solve_body = client.post(
        "/v1/tenants/acme/scenarios/solve", json={"service_level_target": 0.95}
    ).json()

    save_r = client.post(
        "/v1/tenants/acme/scenarios",
        json={"name": "Baseline 95%", "params": solve_body["params"], "result": solve_body},
    )
    assert save_r.status_code == 200
    scenario = save_r.json()
    assert scenario["name"] == "Baseline 95%"
    assert scenario["status"] == "draft"
    assert scenario["committed_at"] is None
    scenario_id = scenario["id"]

    list_r = client.get("/v1/tenants/acme/scenarios")
    assert list_r.status_code == 200
    assert any(s["id"] == scenario_id for s in list_r.json())

    get_r = client.get(f"/v1/tenants/acme/scenarios/{scenario_id}")
    assert get_r.status_code == 200
    assert get_r.json()["id"] == scenario_id

    delete_r = client.delete(f"/v1/tenants/acme/scenarios/{scenario_id}")
    assert delete_r.status_code == 200
    assert delete_r.json() == {"deleted": scenario_id}

    assert client.get(f"/v1/tenants/acme/scenarios/{scenario_id}").status_code == 404


def test_get_unknown_scenario_404():
    client, _ = _client()
    assert client.get("/v1/tenants/acme/scenarios/does-not-exist").status_code == 404


def test_delete_unknown_scenario_404():
    client, _ = _client()
    assert client.delete("/v1/tenants/acme/scenarios/does-not-exist").status_code == 404


def test_commit_scenario_promotes_status_and_returns_audit_event():
    client, store = _client()
    solve_body = client.post("/v1/tenants/acme/scenarios/solve", json={}).json()
    saved = client.post(
        "/v1/tenants/acme/scenarios",
        json={"name": "Commit me", "params": solve_body["params"], "result": solve_body},
    ).json()

    commit_r = client.post(f"/v1/tenants/acme/scenarios/{saved['id']}/commit")
    assert commit_r.status_code == 200
    event = commit_r.json()
    assert event["scenario_id"] == saved["id"]
    assert event["action"] == "commit"
    assert "no emro writeback" in event["note"].lower()

    updated = client.get(f"/v1/tenants/acme/scenarios/{saved['id']}").json()
    assert updated["status"] == "committed"
    assert updated["committed_at"] is not None

    # Audit log accumulates in-memory (not exposed via a route yet — checked at the
    # store level to prove the commit is actually recorded, not just returned once).
    assert len(store.scenario_audit_log()) == 1
    assert store.scenario_audit_log()[0].scenario_name == "Commit me"


def test_commit_unknown_scenario_404():
    client, _ = _client()
    assert client.post("/v1/tenants/acme/scenarios/nope/commit").status_code == 404


def test_store_get_delete_scenario_not_found_raises():
    store = _store()
    with pytest.raises(ScenarioNotFound):
        store.get_scenario("nope")
    with pytest.raises(ScenarioNotFound):
        store.delete_scenario("nope")
    with pytest.raises(ScenarioNotFound):
        store.commit_scenario("nope")


def test_list_scenarios_sorted_newest_first():
    store = _store()
    solved = store.solve_scenario(ScenarioParamsWire())
    first = store.save_scenario("First", ScenarioParamsWire(), solved)
    second = store.save_scenario("Second", ScenarioParamsWire(), solved)
    listed = store.list_scenarios()
    assert [s.id for s in listed] == [second.id, first.id]


# --------------------------------------------------------------------------- #
# Performance test — the real ~21K-key extract used by the local Docker deploy
# (deploy/_local_extract/emro_net, mounted at /data/extract in docker-compose.yml).
# Skipped if that extract isn't present on disk (e.g. CI without the local dataset).
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not (_LOCAL_EXTRACT_DIR / "emro_net").exists(),
    reason="deploy/_local_extract/emro_net not present (local-only real extract)",
)
def test_solve_scenario_latency_under_2s_over_real_extract():
    extract_dir = _LOCAL_EXTRACT_DIR / "emro_net"
    recs_file = _LOCAL_EXTRACT_DIR / "emro_net_recs.json"
    store = PlannerStore.from_snapshot(
        tenant_id="acme",
        extract_dir=str(extract_dir),
        recs_file=str(recs_file),
        now=datetime(2024, 4, 1, tzinfo=UTC),
        pool_by_part=True,
    )
    assert len(store.keys) > 10_000  # sanity: this really is the large real universe

    t0 = time.perf_counter()
    result = store.solve_scenario(ScenarioParamsWire(service_level_target=0.97))
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.0, f"solve took {elapsed:.2f}s over {len(store.keys)} keys"
    assert result.proposed.scored_keys > 0

    # A second solve (warm KeyStats cache) must be fast too — the cache is meant to
    # absorb the per-key feature-store reads once, not once per slider drag.
    t0 = time.perf_counter()
    store.solve_scenario(ScenarioParamsWire(service_level_target=0.90, lead_time_delta_pct=0.3))
    warm_elapsed = time.perf_counter() - t0
    assert warm_elapsed < 2.0, f"warm-cache solve took {warm_elapsed:.2f}s"
