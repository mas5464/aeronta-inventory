import psycopg
import pytest


def test_mirror_tables_exist(pg_admin_conn):
    tables = {r[0] for r in pg_admin_conn.execute(
        "select table_name from information_schema.tables "
        "where table_schema='public'").fetchall()}
    assert {"products","prices","subscriptions","stripe_events"} <= tables

def test_products_prices_public_read_no_write(pg_admin_conn):
    pg_admin_conn.execute(
        "insert into products (id,active,name) values ('prod_x',true,'Growth')")
    pg_admin_conn.execute(
        "insert into prices (id,product_id,active,unit_amount,currency,interval,"
        "metadata) values ('price_x','prod_x',true,29900,'usd','month',"
        "'{\"tier\":\"growth\"}'::jsonb)")
    pg_admin_conn.execute("set role anon")
    assert pg_admin_conn.execute("select count(*) from prices").fetchone()[0] == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg_admin_conn.execute("update prices set unit_amount=1 where id='price_x'")
    pg_admin_conn.execute("reset role")

def test_subscriptions_tenant_scoped_read(pg_admin_conn):
    # Two tenants; a subscription for tenant A; the trax_app role with tenant A's
    # claim sees it, with tenant B's claim does not.
    a = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c4a','A') returning id").fetchone()[0]
    b = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c4b','B') returning id").fetchone()[0]
    pg_admin_conn.execute(
        "insert into subscriptions (id,tenant_id,status,price_id) "
        "values ('sub_x',%s,'active','price_x')", (a,))

    def _as_tenant(tid):
        # pg_admin_conn is autocommit — set_config(..., true) is transaction-local
        # (SET LOCAL semantics), so the role switch + claim + query must share one
        # explicit transaction or the claim never becomes visible to the SELECT.
        with pg_admin_conn.transaction():
            pg_admin_conn.execute("set role trax_app")
            pg_admin_conn.execute(
                "select set_config('request.jwt.claims', %s, true)",
                (f'{{"tenant_id":"{tid}"}}',),
            )
            n = pg_admin_conn.execute("select count(*) from subscriptions").fetchone()[0]
            pg_admin_conn.execute("reset role")
        return n

    assert _as_tenant(a) == 1
    assert _as_tenant(b) == 0
