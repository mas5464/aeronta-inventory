from pathlib import Path

from trax_io_spine.pg.db import apply_migrations, tenant_claims, tenant_conn

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_apply_migrations_idempotent(admin_pool):
    with admin_pool.connection() as conn:
        assert apply_migrations(conn) == []  # session fixture already applied all


def test_migrations_dir_default_points_at_repo_root():
    from trax_io_spine.pg import db

    assert (Path(db.DEFAULT_MIGRATIONS_DIR) / "..").resolve().name == "supabase"


def test_tenant_claims_shape():
    import json

    claims = json.loads(tenant_claims(A, role="admin"))
    assert claims["tenant_id"] == A and claims["tenant_role"] == "admin" and "sub" in claims


def test_tenant_conn_sets_and_clears_claims(pg_pool, admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, 'acme', 'Acme Air') "
            "on conflict (id) do nothing",
            (A,),
        )
        conn.commit()
    with tenant_conn(pg_pool, tenant_uuid=A) as conn:
        assert conn.execute("select public.current_tenant_id()::text").fetchone()[0] == A
    # a FRESH checkout has no residual claims (SET LOCAL died with the transaction)
    with pg_pool.connection() as conn:
        assert conn.execute("select public.current_tenant_id()").fetchone()[0] is None
