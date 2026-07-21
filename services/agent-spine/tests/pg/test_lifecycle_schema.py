"""Isolation + append-only guarantees for the lifecycle tables."""
import json

import psycopg
import pytest

from tests.pg.conftest import as_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _rec_row(tenant: str, rec_id: str) -> tuple:
    rec = {"recommendation_id": rec_id, "part_number": "PN1", "current_location": "MIA"}
    outcome = {"tier": 2}
    return (tenant, rec_id, "PN1", "MIA", 2, "adjust_min_max", 3, 1, 0.9, 1200.5, 10.0,
            True, json.dumps(rec), json.dumps(outcome))


INSERT = (
    "insert into recommendations (tenant_id, rec_id, pn, location, tier, rec_type, "
    "criticality_tier, aog_level, confidence, cost_impact, priority, approvable, rec, outcome) "
    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme', 'Acme Air'), (%s, 'globex', 'Globex Airways') "
            "on conflict (id) do nothing",
            (A, B),
        )
        conn.execute(INSERT, _rec_row(A, "rec-a1"))
        conn.execute(INSERT, _rec_row(B, "rec-b1"))
        conn.commit()


def test_recommendations_isolated(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        ids = [r[0] for r in conn.execute("select rec_id from recommendations").fetchall()]
        assert ids == ["rec-a1"]


def test_cannot_insert_for_other_tenant(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(INSERT, _rec_row(B, "rec-b2"))


def test_decisions_append_only(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        conn.execute(
            "insert into decisions (tenant_id, rec_id, action) values (%s, 'rec-a1', 'approve')",
            (A,),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from decisions")


def test_ledger_append_only_and_isolated(pg_pool):
    entry = json.dumps({"status": "written", "new_values": {"rop": 5}})
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        conn.execute(
            "insert into writeback_ledger (tenant_id, pn, location, version, entry, changed_at)"
            " values (%s, 'PN1', 'MIA', 1, %s, now())",
            (A, entry),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        as_tenant(conn, B)
        assert conn.execute("select count(*) from writeback_ledger").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update writeback_ledger set changed_at = now()")


def test_kill_switch_scoped(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        conn.execute(
            "insert into kill_switches (tenant_id, engaged) values (%s, true) "
            "on conflict (tenant_id) do update set engaged = true",
            (A,),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        as_tenant(conn, B)
        assert conn.execute("select count(*) from kill_switches").fetchone()[0] == 0
