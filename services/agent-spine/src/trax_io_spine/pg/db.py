"""Connection + migration plumbing. See C1 plan Task 6 for the contract.

`tenant_conn` is the ONLY sanctioned Postgres entry point for app code: it pins
the tenant's JWT claims onto the transaction with SET LOCAL so every RLS policy
sees them, and they die with the transaction (no leakage across pool checkouts).
"""
from __future__ import annotations

import json
import uuid as _uuid
from contextlib import contextmanager
from pathlib import Path

from psycopg import Connection
from psycopg_pool import ConnectionPool

DEFAULT_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[5] / "supabase" / "migrations"
)


def make_pool(database_url: str, *, min_size: int = 1, max_size: int = 8) -> ConnectionPool:
    return ConnectionPool(database_url, min_size=min_size, max_size=max_size, open=True)


def apply_migrations(conn: Connection, migrations_dir: Path | None = None) -> list[str]:
    """Apply every not-yet-applied migration in name order; returns names ran."""
    mdir = migrations_dir or DEFAULT_MIGRATIONS_DIR
    conn.execute(
        "create table if not exists public._migrations ("
        "name text primary key, applied_at timestamptz not null default now())"
    )
    applied = {r[0] for r in conn.execute("select name from public._migrations").fetchall()}
    ran: list[str] = []
    for path in sorted(mdir.glob("*.sql")):
        if path.name in applied:
            continue
        conn.execute(path.read_text())
        conn.execute("insert into public._migrations (name) values (%s)", (path.name,))
        ran.append(path.name)
    conn.commit()
    return ran


def tenant_claims(tenant_id: str, role: str = "planner", sub: str | None = None) -> str:
    return json.dumps(
        {"sub": sub or str(_uuid.uuid4()), "tenant_id": tenant_id, "tenant_role": role}
    )


@contextmanager
def tenant_conn(
    pool: ConnectionPool, *, tenant_uuid: str, role: str = "planner", sub: str | None = None
):
    with pool.connection() as conn:
        conn.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (tenant_claims(tenant_uuid, role=role, sub=sub),),
        )
        yield conn
        # pool.connection() context commits on clean exit / rolls back on error


def resolve_tenant_uuid(conn: Connection, slug: str) -> str | None:
    row = conn.execute("select id::text from public.tenants where slug = %s", (slug,)).fetchone()
    return row[0] if row else None
