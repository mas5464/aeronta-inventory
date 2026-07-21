"""Decision-lifecycle parity + durability (fresh store instance sees decisions)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.models import BulkApproveFilter, RejectReason, TaskStatus
from trax_io_spine.bff.store import KillSwitchEngaged, PlannerStore
from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture()
def stores(admin_pool, pg_pool):
    mem = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    report = seed_store(admin_pool, store=mem, slug="acme-t10", name="Acme Air")
    pg = PgPlannerStore(pg_pool, tenant_slug="acme-t10", tenant_uuid=report.tenant_uuid)
    return mem, pg, report


def _first_approvable(store):
    return next(r.recommendation_id for r in store.queue() if r.approvable)


def test_approve_parity_and_durability(stores, pg_pool):
    mem, pg, report = stores
    rid = _first_approvable(mem)
    m = mem.approve(rid)
    p = pg.approve(rid)
    assert (p.status, p.writeback.status) == (m.status, m.writeback.status)
    # durability: a FRESH store instance (new process, same DB) sees the decision
    fresh = PgPlannerStore(pg_pool, tenant_slug="acme-t10", tenant_uuid=report.tenant_uuid)
    assert fresh.detail(rid).status is TaskStatus.APPROVED
    assert fresh.history(pn=p.writeback.pn, location=p.writeback.location)


def test_reject_and_defer(stores):
    _, pg, _ = stores
    rids = [r.recommendation_id for r in pg.queue()]
    r = pg.reject(rids[0], RejectReason.WRONG_FOR_FLEET, "bad demand rows")
    d = pg.defer(rids[1] if len(rids) > 1 else rids[0])
    assert r.status is TaskStatus.REJECTED and d.status is TaskStatus.DEFERRED


def test_bulk_approve_parity(stores):
    mem, pg, _ = stores
    f = BulkApproveFilter(tiers=None, max_delta_pct=None, criticality_min=None, types=None)
    m_count, _ = mem.bulk_approve(f)
    p_count, _ = pg.bulk_approve(f)
    assert p_count == m_count


def test_kill_switch_blocks_and_persists(stores, pg_pool):
    _, pg, report = stores
    pg.set_kill_switch(True)
    assert pg.kill_switch is True
    with pytest.raises(KillSwitchEngaged):
        pg.approve(_first_approvable(pg))
    fresh = PgPlannerStore(pg_pool, tenant_slug="acme-t10", tenant_uuid=report.tenant_uuid)
    assert fresh.kill_switch is True
    pg.set_kill_switch(False)


def test_decisions_are_recorded(stores, admin_pool):
    _, pg, report = stores
    pg.reject(pg.queue()[0].recommendation_id, RejectReason.WRONG_FOR_FLEET)
    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from decisions where tenant_id = %s::uuid and action='reject'",
            (report.tenant_uuid,),
        ).fetchone()[0]
        assert n >= 1


def test_filter_parity(stores):
    """Non-vacuous parity check for the tier/type/aog_min filters (Task 9 review gap)."""
    mem, pg, _ = stores
    rows = mem.queue()
    sample = rows[0]
    tier, type_, aog_min = sample.tier, sample.type, sample.aog_risk_level

    def _ids(rows):
        return [r.recommendation_id for r in rows]

    m_tier = mem.list_queue_all(tier=tier)
    p_tier = pg.list_queue_all(tier=tier)
    assert _ids(p_tier) == _ids(m_tier)
    assert p_tier

    m_type = mem.list_queue_all(type_=type_)
    p_type = pg.list_queue_all(type_=type_)
    assert _ids(p_type) == _ids(m_type)
    assert p_type

    m_aog = mem.list_queue_all(aog_min=aog_min)
    p_aog = pg.list_queue_all(aog_min=aog_min)
    assert _ids(p_aog) == _ids(m_aog)
    assert p_aog


def test_decisions_invalidate_bvr_cache(stores, admin_pool):
    """Verify that decisions (reject, set_kill_switch) invalidate the bvr_cache."""
    _, pg, report = stores

    def seed_cache():
        with admin_pool.connection() as conn:
            conn.execute(
                "insert into bvr_cache (tenant_id, report) values (%s::uuid, '{}')"
                " on conflict (tenant_id) do update set report = '{}'",
                (report.tenant_uuid,),
            )
            conn.commit()

    def cache_rows():
        with admin_pool.connection() as conn:
            return conn.execute(
                "select count(*) from bvr_cache where tenant_id = %s::uuid",
                (report.tenant_uuid,),
            ).fetchone()[0]

    # Test: reject invalidates cache
    seed_cache()
    assert cache_rows() == 1
    pg.reject(pg.queue()[0].recommendation_id, RejectReason.WRONG_FOR_FLEET)
    assert cache_rows() == 0

    # Test: set_kill_switch(True) invalidates cache
    seed_cache()
    assert cache_rows() == 1
    pg.set_kill_switch(True)
    assert cache_rows() == 0

    # Test: set_kill_switch(False) invalidates cache
    seed_cache()
    assert cache_rows() == 1
    pg.set_kill_switch(False)
    assert cache_rows() == 0
