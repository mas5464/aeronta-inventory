"""The hook is the ONLY writer of tenant claims — pin its selection semantics."""
import json

import pytest

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
U_MULTI = "00000000-0000-0000-0000-0000000000cc"
U_NONE = "00000000-0000-0000-0000-0000000000dd"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme', 'Acme Air'), (%s, 'globex', 'Globex Airways') "
            "on conflict (id) do nothing",
            (A, B),
        )
        # U_MULTI: member of both; globex membership is newer
        conn.execute(
            "insert into memberships (user_id, tenant_id, role, created_at) values "
            "(%s, %s, 'planner', now() - interval '2 days'), "
            "(%s, %s, 'admin',   now() - interval '1 day') on conflict do nothing",
            (U_MULTI, A, U_MULTI, B),
        )
        conn.commit()


def _hook(conn, user_id: str, claims: dict) -> dict:
    row = conn.execute(
        "select public.custom_access_token_hook(%s::jsonb)",
        (json.dumps({"user_id": user_id, "claims": claims}),),
    ).fetchone()
    return row[0]["claims"]


def test_default_is_most_recent_membership(admin_pool):
    with admin_pool.connection() as conn:
        claims = _hook(conn, U_MULTI, {"sub": U_MULTI})
        assert claims["tenant_id"] == B
        assert claims["tenant_role"] == "admin"


def test_requested_tenant_honored_when_member(admin_pool):
    with admin_pool.connection() as conn:
        claims = _hook(conn, U_MULTI, {"sub": U_MULTI, "tenant_id": A})
        assert claims["tenant_id"] == A
        assert claims["tenant_role"] == "planner"


def test_requested_tenant_ignored_when_not_member(admin_pool):
    with admin_pool.connection() as conn:
        evil = "99999999-9999-9999-9999-999999999999"
        claims = _hook(conn, U_MULTI, {"sub": U_MULTI, "tenant_id": evil})
        # falls back to a REAL membership, never passes the foreign claim through
        assert claims["tenant_id"] in (A, B)


def test_no_membership_strips_claims(admin_pool):
    with admin_pool.connection() as conn:
        claims = _hook(conn, U_NONE, {"sub": U_NONE, "tenant_id": A})
        assert "tenant_id" not in claims and "tenant_role" not in claims


def test_malformed_user_id_strips_claims(admin_pool):
    with admin_pool.connection() as conn:
        claims = _hook(conn, "not-a-uuid", {"sub": "not-a-uuid", "tenant_id": A})
        assert "tenant_id" not in claims and "tenant_role" not in claims


def test_malformed_requested_tenant_falls_back(admin_pool):
    with admin_pool.connection() as conn:
        claims = _hook(conn, U_MULTI, {"sub": U_MULTI, "tenant_id": "zzz-junk"})
        assert claims["tenant_id"] in (A, B)
