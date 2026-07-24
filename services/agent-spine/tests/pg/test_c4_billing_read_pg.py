import pytest

from trax_io_spine.bff.billing import billing_summary
from trax_io_spine.pg.db import tenant_conn


def test_billing_summary_reads_tenant_and_counts_keys(pg_admin_conn):
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name,plan_tier,key_quota,subscription_status) "
        "values ('c4bill','B','growth',25000,'active') returning id"
    ).fetchone()[0]
    pg_admin_conn.execute(
        "insert into part_keys (tenant_id,pn,location,key_stats) "
        "values (%s,'P1','JFK','{}'::jsonb),(%s,'P2','JFK','{}'::jsonb)",
        (tid, tid),
    )
    s = billing_summary(pg_admin_conn, str(tid))
    assert s.plan_tier == "growth" and s.key_quota == 25000
    assert s.subscription_status == "active" and s.keys_used == 2


def test_billing_summary_unknown_tenant_raises(pg_admin_conn):
    with pytest.raises(ValueError):
        billing_summary(pg_admin_conn, "00000000-0000-0000-0000-000000000000")


def _sub_status_query(conn, t_uuid: str) -> str | None:
    """Mirrors bff/asgi.py's `_sub_status_for` query body exactly."""
    row = conn.execute(
        "select subscription_status::text from tenants where id = %s::uuid",
        (t_uuid,),
    ).fetchone()
    return row[0] if row else None


def test_sub_status_bare_pool_connection_is_rls_blocked(pg_admin_conn, pg_pool):
    """Regression for the cross-task defect fixed in bff/asgi.py's
    `_sub_status_for`: `tenants` is RLS-protected (`tenants_select`, keyed on
    current_tenant_id()) and trax_app has NOBYPASSRLS in production. A bare
    `pg_pool.connection()` checkout — the OLD `_sub_status_for` body — carries
    no `request.jwt.claims` GUC, so RLS filters the row away entirely: this
    documents that the old pattern returns None even for a real, active
    tenant (which the write-gate then reads as "no subscription" -> 402s
    every write for every tenant)."""
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name,plan_tier,key_quota,subscription_status) "
        "values ('c4subgate','G','growth',25000,'active') returning id"
    ).fetchone()[0]

    with pg_pool.connection() as c:
        assert _sub_status_query(c, str(tid)) is None


def test_sub_status_tenant_conn_sees_active_status(pg_admin_conn, pg_pool):
    """The fixed `_sub_status_for` pattern: `tenant_conn` sets the tenant's
    claims GUC on the transaction, so RLS resolves the tenant's own row and
    the real 'active' status comes back."""
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name,plan_tier,key_quota,subscription_status) "
        "values ('c4subgate2','G2','growth',25000,'active') returning id"
    ).fetchone()[0]

    with tenant_conn(pg_pool, tenant_uuid=str(tid)) as c:
        assert _sub_status_query(c, str(tid)) == "active"


def test_billing_summary_rls_scopes_keys_used_to_own_tenant(pg_admin_conn, pg_pool):
    """`billing_summary` over a trax_app-role `tenant_conn` (RLS enforced, not
    the superuser `pg_admin_conn` used by the other tests in this file) must
    return only the calling tenant's plan/status/quota and a `keys_used`
    count scoped to that tenant's own `part_keys` rows — proving RLS (on
    `tenants` and `part_keys`) plus the explicit `tenant_id = %s` filter work
    together, with no cross-tenant leakage into the count."""
    tid_a = pg_admin_conn.execute(
        "insert into tenants (slug,name,plan_tier,key_quota,subscription_status) "
        "values ('c4billa','BillA','starter',5000,'active') returning id"
    ).fetchone()[0]
    tid_b = pg_admin_conn.execute(
        "insert into tenants (slug,name,plan_tier,key_quota,subscription_status) "
        "values ('c4billb','BillB','growth',25000,'trialing') returning id"
    ).fetchone()[0]
    pg_admin_conn.execute(
        "insert into part_keys (tenant_id,pn,location,key_stats) "
        "values (%s,'A1','JFK','{}'::jsonb),(%s,'A2','JFK','{}'::jsonb)",
        (tid_a, tid_a),
    )
    pg_admin_conn.execute(
        "insert into part_keys (tenant_id,pn,location,key_stats) "
        "values (%s,'B1','JFK','{}'::jsonb),(%s,'B2','JFK','{}'::jsonb),"
        "(%s,'B3','JFK','{}'::jsonb)",
        (tid_b, tid_b, tid_b),
    )

    with tenant_conn(pg_pool, tenant_uuid=str(tid_a)) as c:
        s_a = billing_summary(c, str(tid_a))
    assert s_a.plan_tier == "starter"
    assert s_a.subscription_status == "active"
    assert s_a.key_quota == 5000
    assert s_a.keys_used == 2

    with tenant_conn(pg_pool, tenant_uuid=str(tid_b)) as c:
        s_b = billing_summary(c, str(tid_b))
    assert s_b.plan_tier == "growth"
    assert s_b.subscription_status == "trialing"
    assert s_b.key_quota == 25000
    assert s_b.keys_used == 3
