"""bff/whoami.py's DB-backed read, against real Postgres as the real `trax_app`
role (NOT `pg_admin_conn`, a superuser — a superuser-backed pool would pass
these assertions even if the wiring were completely broken, which is exactly
how the `_sub_status_for` bug survived review once already; see
.claude/memory/lessons.md, "Bare pool reads on RLS'd tables silently return
zero rows as trax_app").

`tenants_for_current_user()` is SECURITY DEFINER (bypasses RLS by design —
see the migration's own docstring) but it still depends entirely on the
`request.jwt.claims` GUC's `sub`: `auth.jwt()->>'sub'` is only populated when
that GUC is set. A bare pool connection has it unset, so the function's
`m.user_id = (auth.jwt()->>'sub')::uuid` filter compares against SQL NULL and
matches nothing — the exact same silent-zero-rows failure mode as a
straightforward RLS block, for the same underlying reason (missing claims
GUC), even though the mechanism (NULL-comparison vs. a policy) differs.
"""
import uuid

from trax_io_spine.bff.whoami import build_whoami_response, tenants_for
from trax_io_spine.pg.db import tenant_conn


def test_bare_pool_connection_sees_no_memberships(pg_admin_conn, pg_pool):
    user_id = str(uuid.uuid4())
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-who-bare','Bare') returning id"
    ).fetchone()[0]
    pg_admin_conn.execute(
        "insert into memberships (user_id,tenant_id,role) values (%s,%s,'owner')",
        (user_id, tid),
    )

    with pg_pool.connection() as c:
        assert tenants_for(c) == []


def test_tenant_conn_sees_only_the_callers_own_memberships(pg_admin_conn, pg_pool):
    u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
    a = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-who-a','A') returning id"
    ).fetchone()[0]
    b = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-who-b','B') returning id"
    ).fetchone()[0]
    pg_admin_conn.execute(
        "insert into memberships (user_id,tenant_id,role) values "
        "(%s,%s,'owner'),(%s,%s,'planner')",
        (u1, a, u1, b),
    )
    pg_admin_conn.execute(
        "insert into memberships (user_id,tenant_id,role) values (%s,%s,'owner')",
        (u2, b),
    )

    with tenant_conn(pg_pool, tenant_uuid=str(a), sub=u1) as c:
        refs_u1 = tenants_for(c)
    assert [(r.slug, r.role) for r in refs_u1] == [
        ("c5-who-a", "owner"),
        ("c5-who-b", "planner"),
    ]
    # No cross-caller leakage: u2 sees ONLY its own membership, never u1's.
    with tenant_conn(pg_pool, tenant_uuid=str(b), sub=u2) as c:
        refs_u2 = tenants_for(c)
    assert [(r.slug, r.role) for r in refs_u2] == [("c5-who-b", "owner")]


def test_whoami_reader_shape_picks_active_from_the_real_membership_list(
    pg_admin_conn, pg_pool
):
    """Mirrors `bff/asgi.py`'s `_whoami_reader` body exactly: tenant_conn +
    tenants_for + build_whoami_response, over a real trax_app connection —
    proving the full production reader shape (not just the raw query) works
    end to end, including which tenant lands in `active`."""
    u1 = str(uuid.uuid4())
    a = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-who-shape-a','A') returning id"
    ).fetchone()[0]
    b = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-who-shape-b','B') returning id"
    ).fetchone()[0]
    pg_admin_conn.execute(
        "insert into memberships (user_id,tenant_id,role) values "
        "(%s,%s,'owner'),(%s,%s,'planner')",
        (u1, a, u1, b),
    )

    def _whoami_reader(sub: str, active_tenant_uuid: str | None):
        with tenant_conn(pg_pool, tenant_uuid=active_tenant_uuid or str(a), sub=sub) as c:
            tenants = tenants_for(c)
        return build_whoami_response(sub, active_tenant_uuid, tenants)

    resp = _whoami_reader(u1, str(b))
    assert resp.user_id == u1
    assert resp.active is not None and resp.active.slug == "c5-who-shape-b"
    assert {t.slug for t in resp.tenants} == {"c5-who-shape-a", "c5-who-shape-b"}

    # No active tenant claim at all (e.g. a stale token): tenants still list,
    # active is None rather than guessing.
    resp_no_active = _whoami_reader(u1, None)
    assert resp_no_active.active is None
    assert {t.slug for t in resp_no_active.tenants} == {
        "c5-who-shape-a",
        "c5-who-shape-b",
    }


def test_unknown_caller_returns_empty_not_an_error(pg_admin_conn, pg_pool):
    """A verified JWT for a user with zero memberships (e.g. mid-signup,
    before any tenant exists) must come back as an empty list, not a crash or
    a leak of anyone else's tenants."""
    stranger = str(uuid.uuid4())
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-who-stranger','S') returning id"
    ).fetchone()[0]
    pg_admin_conn.execute(
        "insert into memberships (user_id,tenant_id,role) values (%s,%s,'owner')",
        (str(uuid.uuid4()), tid),
    )

    with tenant_conn(pg_pool, tenant_uuid=str(tid), sub=stranger) as c:
        assert tenants_for(c) == []
