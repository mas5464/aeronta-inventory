"""Conformance of PgWritebackTarget with InMemoryWritebackTarget semantics.

Every test here runs the SAME scenario against both targets and asserts the
observable results match — the in-memory target is the executable spec.
"""
from datetime import UTC, datetime, timedelta

import pytest

from tests.pg.conftest import as_tenant  # noqa: F401  (fixtures)
from trax_io_spine.contracts import (
    RollbackRequest,
    RollbackStatus,
    WritebackRequest,
    WritebackStatus,
)
from trax_io_spine.pg.writeback import PgWritebackTarget
from trax_io_spine.writeback.target import InMemoryWritebackTarget

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SLUG = "acme"


def _req(pn="PN1", loc="MIA", *, idem="k1", shadow=False, rop=5):
    return WritebackRequest(
        tenant_id=SLUG, pn=pn, location=loc, rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key=idem, tier=2,
        shadow=shadow,
    )


@pytest.fixture()
def targets(pg_pool, admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, %s, 'Acme Air') "
            "on conflict (id) do nothing",
            (A, SLUG),
        )
        conn.execute("delete from writeback_ledger where tenant_id = %s", (A,))
        conn.commit()
    return (
        InMemoryWritebackTarget(),
        PgWritebackTarget(pg_pool, tenant_uuid=A),
    )


def test_write_then_history_matches(targets):
    mem, pg = targets
    for t in (mem, pg):
        r1 = t.write(_req(idem="k1", rop=5))
        r2 = t.write(_req(idem="k2", rop=7))
        assert (r1.status, r1.old_values, r2.old_values["rop"]) == (
            WritebackStatus.WRITTEN, None, 5,
        )
    mh = mem.get_history(tenant_id=SLUG, pn="PN1", location="MIA")
    ph = pg.get_history(tenant_id=SLUG, pn="PN1", location="MIA")
    assert [(e.version, e.status, e.parent_version) for e in mh] == [
        (e.version, e.status, e.parent_version) for e in ph
    ]


def test_idempotent_replay(targets):
    _, pg = targets
    first = pg.write(_req(idem="same"))
    again = pg.write(_req(idem="same"))
    assert again.status is WritebackStatus.WRITTEN
    assert again.new_values == first.new_values


def test_open_order_defers_without_ledger_entry(pg_pool, admin_pool, targets):
    _, _ = targets
    pg = PgWritebackTarget(pg_pool, tenant_uuid=A, open_orders={(SLUG, "PN1", "MIA")})
    r = pg.write(_req(idem="k-def"))
    assert r.status is WritebackStatus.DEFERRED_OPEN_ORDER
    assert pg.get_history(tenant_id=SLUG, pn="PN1", location="MIA") == ()


def test_shadow_records_but_does_not_change_levels(targets):
    _, pg = targets
    pg.write(_req(idem="k1", rop=5))
    pg.write(_req(idem="k-sh", rop=9, shadow=True))
    written = pg.write(_req(idem="k3", rop=11))
    assert written.old_values["rop"] == 5  # shadow write did not become current
    statuses = [e.status for e in pg.get_history(tenant_id=SLUG, pn="PN1", location="MIA")]
    assert WritebackStatus.SHADOWED in statuses


def test_rollback_parity(targets):
    mem, pg = targets
    now = datetime.now(UTC)
    for t in (mem, pg):
        t.write(_req(idem="k1", rop=5))
        t.write(_req(idem="k2", rop=7))
        res = t.rollback(RollbackRequest(
            tenant_id=SLUG, pn="PN1", location="MIA",
            principal="planner", reason="test", requested_at=now,
        ))
        assert res.status is RollbackStatus.ROLLED_BACK
        assert res.to_values["rop"] == 5
    assert pg.rollback(RollbackRequest(
        tenant_id=SLUG, pn="PN9", location="ZZZ",
        principal="planner", reason="test", requested_at=now,
    )).status is RollbackStatus.NOTHING_TO_REVERT


def test_rollback_outside_window(pg_pool, targets):
    _, pg = targets
    pg.write(_req(idem="k1", rop=5))
    pg.write(_req(idem="k2", rop=7))
    res = pg.rollback(RollbackRequest(
        tenant_id=SLUG, pn="PN1", location="MIA", principal="planner",
        reason="test", requested_at=datetime.now(UTC) + timedelta(days=91),
    ))
    assert res.status is RollbackStatus.OUTSIDE_WINDOW


def test_write_default_principal_is_agent_spine(targets):
    """No explicit principal configured => writes stay attributed to the
    autonomous agent identity (unchanged default, C3 Task 0a)."""
    _, pg = targets
    pg.write(_req(idem="k-default-principal"))
    entries = pg.get_history(tenant_id=SLUG, pn="PN1", location="MIA")
    assert entries[-1].changed_by_principal == "agent-spine"


def test_write_records_configured_principal(pg_pool, targets):
    """PgWritebackTarget(principal=...) attributes writes to that verified
    caller instead of the 'agent-spine' default (C3 Task 0a)."""
    custom = PgWritebackTarget(pg_pool, tenant_uuid=A, principal="user-42")
    r = custom.write(_req(idem="k-custom-principal"))
    assert r.status is WritebackStatus.WRITTEN
    entries = custom.get_history(tenant_id=SLUG, pn="PN1", location="MIA")
    assert entries[-1].changed_by_principal == "user-42"
