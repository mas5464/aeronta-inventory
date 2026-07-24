import pytest

from trax_io_spine.bff.billing import billing_summary


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
