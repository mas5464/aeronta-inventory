import psycopg
import pytest


def _superuser(pg_url: str):
    return psycopg.connect(pg_url)  # conftest's admin URL fixture — see below


def test_subscription_status_enum_values(pg_admin_conn):
    rows = pg_admin_conn.execute(
        "select enumlabel from pg_enum e join pg_type t on t.oid=e.enumtypid "
        "where t.typname='subscription_status' order by enumsortorder"
    ).fetchall()
    assert [r[0] for r in rows] == [
        "trialing","active","past_due","canceled","incomplete",
        "incomplete_expired","unpaid","paused",
    ]

def test_tenants_billing_columns(pg_admin_conn):
    cols = {r[0] for r in pg_admin_conn.execute(
        "select column_name from information_schema.columns "
        "where table_name='tenants' and table_schema='public'").fetchall()}
    assert {"stripe_customer_id","stripe_subscription_id","subscription_status",
            "current_period_end","trial_ends_at"} <= cols

def test_plan_tiers_seeded(pg_admin_conn):
    rows = dict(pg_admin_conn.execute(
        "select tier, key_quota from plan_tiers order by sort").fetchall())
    assert rows == {"starter":5000,"growth":25000,"scale":100000}

def test_plan_tiers_public_read_rls(pg_admin_conn):
    # anon can read active tiers (public pricing page); anon cannot write.
    pg_admin_conn.execute("set role anon")
    got = pg_admin_conn.execute("select count(*) from plan_tiers").fetchone()[0]
    assert got == 3
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg_admin_conn.execute("insert into plan_tiers (tier,key_quota) values ('x',1)")
    pg_admin_conn.execute("reset role")
