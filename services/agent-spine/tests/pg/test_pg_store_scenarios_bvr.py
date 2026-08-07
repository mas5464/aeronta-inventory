"""Scenario + BVR parity. BVR compared minus generated_at (wall-clock differs)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.models import ScenarioParamsWire
from trax_io_spine.bff.store import PlannerStore, ScenarioNotFound
from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine" / "examples" / "extract_sample"
)

# tests/bff/test_scenario.py exposes no importable DEFAULT_PARAMS symbol — every
# solve/save call there just uses a bare ScenarioParamsWire(); lifted verbatim here.
DEFAULT_PARAMS = ScenarioParamsWire()


@pytest.fixture()
def stores(admin_pool, pg_pool, seed_pending_recommendations):
    mem = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    seed_pending_recommendations(mem)
    report = seed_store(admin_pool, store=mem, slug="acme-t12", name="Acme Air")
    return mem, PgPlannerStore(pg_pool, tenant_slug="acme-t12", tenant_uuid=report.tenant_uuid)


def _bvr_dict(store):
    d = store.bvr().model_dump(mode="json")
    d.pop("generated_at", None)
    # `period.generated_at`/`decision_window_start`/`decision_window_end` are all
    # real wall-clock timestamps (the latter two from ledger `changed_at`, written
    # at each store's own `approve()` call) — mem and pg act at different instants
    # even in the same test, so these three are excluded the same way `generated_at`
    # is, alongside `extract_date`/`label` which stay content (and do get compared).
    for field in ("generated_at", "decision_window_start", "decision_window_end"):
        d["period"].pop(field, None)
    # The pg fixture intentionally seeds under a Postgres-only slug ("acme-t12",
    # distinct from the in-memory tenant "acme") to avoid colliding with other pg
    # test files' tenant rows sharing the session-scoped pools (same convention as
    # Tasks 9-11 — see tests/pg/test_pg_store_actions.py). `bvr()`'s `tenant_id`
    # field is a pass-through identity label, not derived computation, so it's
    # excluded from the parity comparison the same way the timestamps are.
    d.pop("tenant_id", None)
    return d


def _scenario_dict_without_tenant_fingerprint(result):
    payload = result.model_dump(mode="json")
    payload.pop("fingerprint")
    return payload


def test_bvr_parity_and_invalidation(stores):
    mem, pg = stores
    assert _bvr_dict(pg) == _bvr_dict(mem)
    rid = next(r.recommendation_id for r in mem.queue() if r.approvable)
    mem.approve(rid)
    pg.approve(rid)
    assert _bvr_dict(pg) == _bvr_dict(mem)


def test_bvr_cache_round_trip(stores, admin_pool):
    _, pg = stores
    first = _bvr_dict(pg)   # computes + caches
    second = _bvr_dict(pg)  # must come from bvr_cache
    assert first == second
    with admin_pool.connection() as conn:
        row = conn.execute(
            "select report from bvr_cache where tenant_id = %s::uuid", (pg._uuid,)
        ).fetchone()
    assert row is not None, "bvr_cache must have a real row after the first bvr() call"


def test_scenario_solve_and_lifecycle(stores):
    mem, pg = stores

    m = mem.solve_scenario(DEFAULT_PARAMS)
    p = pg.solve_scenario(DEFAULT_PARAMS)
    assert _scenario_dict_without_tenant_fingerprint(p) == (
        _scenario_dict_without_tenant_fingerprint(m)
    )
    assert p.fingerprint != m.fingerprint

    # save_scenario's real signature is (name, params, result) — not (name, params)
    # as the original brief pseudocode guessed (see bff/store.py:1058-1070).
    saved = pg.save_scenario(name="test", params=DEFAULT_PARAMS, result=p)
    assert pg.get_scenario(saved.id).name == "test"
    assert len(pg.list_scenarios()) == 1

    event = pg.commit_scenario(saved.id)
    assert event.scenario_id == saved.id
    assert event.action == "commit"
    log = pg.scenario_audit_log()
    assert len(log) == 1
    assert log[0].scenario_id == saved.id
    assert pg.get_scenario(saved.id).status.value == "committed"

    pg.delete_scenario(saved.id)
    with pytest.raises(ScenarioNotFound):
        pg.get_scenario(saved.id)
    with pytest.raises(ScenarioNotFound):
        pg.delete_scenario(saved.id)
    with pytest.raises(ScenarioNotFound):
        pg.commit_scenario(saved.id)


def test_repair_scenario_inputs_are_persisted_hydrated_and_solved_at_v2(
    stores,
    admin_pool,
):
    mem, pg = stores
    params = ScenarioParamsWire(
        repair_tat_delta_pct=0.4,
        procurement_lead_time_delta_pct=0.2,
    )

    expected = mem.solve_scenario(params)
    actual = pg.solve_scenario(params)

    assert _scenario_dict_without_tenant_fingerprint(actual) == (
        _scenario_dict_without_tenant_fingerprint(expected)
    )
    assert actual.fingerprint != expected.fingerprint
    assert actual.contract_version == "scenario-solve.v2"
    assert actual.fingerprint is not None
    assert actual.repair_current is not None
    assert actual.repair_proposed is not None
    with admin_pool.connection() as conn:
        payload = conn.execute(
            "select payload from tenant_snapshots "
            "where tenant_id = %s::uuid and kind = 'scenario_inputs'",
            (pg._uuid,),
        ).fetchone()[0]
    assert payload["contract_version"] == "scenario-inputs.v1"
    assert payload["source_tenant_id"] == mem.tenant_id
    assert len(payload["repair_inputs"]) == len(mem._repair_scenario_inputs())


def test_pg_save_resolves_stale_or_cross_tenant_client_result_authoritatively(
    stores,
    admin_pool,
    pg_pool,
    seed_pending_recommendations,
):
    _mem, pg = stores
    other_mem = PlannerStore.from_extract(
        tenant_id="acme-t12-other",
        extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    seed_pending_recommendations(other_mem)
    other_report = seed_store(
        admin_pool,
        store=other_mem,
        slug="acme-t12-other",
        name="Other Air",
    )
    other = PgPlannerStore(
        pg_pool,
        tenant_slug="acme-t12-other",
        tenant_uuid=other_report.tenant_uuid,
    )
    params = ScenarioParamsWire(repair_tat_delta_pct=0.35)
    foreign_result = other.solve_scenario(DEFAULT_PARAMS)

    saved = pg.save_scenario(
        name="Authoritative repair scenario",
        params=params,
        result=foreign_result,
    )
    authoritative = pg.solve_scenario(params)

    assert saved.result == authoritative
    assert saved.result.fingerprint != foreign_result.fingerprint
    assert other.list_scenarios() == []
    with pytest.raises(ScenarioNotFound):
        other.get_scenario(saved.id)
    pg.delete_scenario(saved.id)
