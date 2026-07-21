"""Isolation for view/scenario tables; app role is read-only on seeded views."""
import json

import psycopg
import pytest

from tests.pg.conftest import as_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme', 'Acme Air'), (%s, 'globex', 'Globex Airways') "
            "on conflict (id) do nothing",
            (A, B),
        )
        conn.execute(
            "insert into part_keys (tenant_id, pn, location, key_stats) values "
            "(%s, 'PN1', 'MIA', %s), (%s, 'PN9', 'FRA', %s) on conflict do nothing",
            (A, json.dumps({"unit_cost": 10}), B, json.dumps({"unit_cost": 99})),
        )
        conn.execute(
            "insert into tenant_snapshots (tenant_id, kind, payload) values "
            "(%s, 'forecast_summary', %s) on conflict do nothing",
            (A, json.dumps({"keys": 1})),
        )
        conn.commit()


def test_part_keys_isolated(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        assert [r[0] for r in conn.execute("select pn from part_keys").fetchall()] == ["PN1"]


def test_seeded_views_read_only_for_app(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into part_keys (tenant_id, pn, location, key_stats) "
                "values (%s, 'PN2', 'MIA', '{}')",
                (A,),
            )


def test_snapshots_isolated(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, B)
        assert conn.execute("select count(*) from tenant_snapshots").fetchone()[0] == 0


def test_scenarios_rw_isolated(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        conn.execute(
            "insert into scenarios (tenant_id, scenario_id, payload) values (%s, 's1', '{}')",
            (A,),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        as_tenant(conn, B)
        assert conn.execute("select count(*) from scenarios").fetchone()[0] == 0
