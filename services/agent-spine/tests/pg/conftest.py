"""Postgres test harness for the C1 pg layer.

Boots ONE throwaway Postgres 16 container per session (testcontainers), applies
the auth shim + every migration in supabase/migrations/ in name order, then hands
out two pools: `admin_pool` (superuser — seeding + cross-tenant assertions) and
`pg_pool` (role trax_app, NOBYPASSRLS — what the BFF uses; RLS is real here).

Docker-unavailable => whole directory skips (repo convention: env-gated infra
tests skip clean, they never fail the suite).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trax_io_spine.pg.db import apply_migrations

try:  # docker may be absent (CI matrix, bare laptops)
    from testcontainers.postgres import PostgresContainer

    _DOCKER = True
except Exception:  # pragma: no cover
    _DOCKER = False

AUTH_SHIM = Path(__file__).parent / "auth_shim.sql"


@pytest.fixture(scope="session")
def _container():
    if not _DOCKER:
        pytest.skip("docker/testcontainers unavailable")
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def admin_pool(_container):
    from psycopg_pool import ConnectionPool

    url = _container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    with ConnectionPool(url, min_size=1, max_size=4) as pool:
        with pool.connection() as conn:
            conn.execute(AUTH_SHIM.read_text())
            conn.commit()
            apply_migrations(conn)
        yield pool


@pytest.fixture(scope="session")
def pg_pool(_container, admin_pool):
    from psycopg_pool import ConnectionPool

    url = _container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    # swap credentials in the URL for the RLS-enforced app role
    app_url = url.replace(_container.username, "trax_app", 1).replace(
        _container.password, "trax_app", 1
    )
    with ConnectionPool(app_url, min_size=1, max_size=4) as pool:
        yield pool


@pytest.fixture(autouse=True)
def _cleanup_jobs(admin_pool):
    """Clean up ALL jobs after each test to avoid cross-test interference."""
    yield
    with admin_pool.connection() as conn:
        conn.execute("delete from jobs")
        conn.commit()


def as_tenant(conn, tenant_id: str, role: str = "planner", sub: str | None = None) -> None:
    """Impersonate a tenant member for the CURRENT transaction (SET LOCAL)."""
    claims = json.dumps(
        {"sub": sub or "00000000-0000-0000-0000-0000000000aa",
         "tenant_id": tenant_id, "tenant_role": role}
    )
    conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
