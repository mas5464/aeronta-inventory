"""GET /v1/auth/whoami — who the caller is and which tenants they belong to.

Deliberately OUTSIDE /v1/tenants/{slug}/… (like activate-tenant): the caller
may have no active tenant at all (mid-signup), and there is no slug to match.
Replaces apps/web's build-time VITE_TENANT_SLUGS map.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class TenantRef(BaseModel):
    tenant_uuid: str
    slug: str
    name: str
    role: str


class WhoamiResponse(BaseModel):
    user_id: str
    active: TenantRef | None
    tenants: list[TenantRef]


def tenants_for(conn) -> list[TenantRef]:
    """The caller's own memberships, via `public.tenants_for_current_user()`
    (migration 20260724000013 — SECURITY DEFINER, no arguments, derives the
    caller solely from `auth.jwt()->>'sub'`).

    That function depends ENTIRELY on the `request.jwt.claims` GUC carrying
    the caller's `sub` — it has no other way to know who is asking. `conn`
    MUST therefore be a `tenant_conn(...)` checkout (see pg/db.py), never a
    bare pool connection: with no claims set, `auth.jwt()->>'sub'` is null
    and the function silently returns zero rows for EVERY caller (not an
    error — see bff/asgi.py's `_whoami_reader` for the production wiring and
    tests/pg/test_c5_whoami_reader.py for the regression proof of both
    halves of this, matching the pattern documented in
    .claude/memory/lessons.md, "Bare pool reads on RLS'd tables silently
    return zero rows as trax_app").
    """
    rows = conn.execute(
        "select tenant_uuid::text, slug, name, role "
        "from public.tenants_for_current_user()"
    ).fetchall()
    return [
        TenantRef(tenant_uuid=r[0], slug=r[1], name=r[2], role=r[3]) for r in rows
    ]


def build_whoami_response(
    sub: str, active_tenant_uuid: str | None, tenants: list[TenantRef]
) -> WhoamiResponse:
    """Pick the caller's currently-active tenant (per their JWT `tenant_id`
    claim) out of their full membership list. Pure/no-DB so it's cheap to
    unit test in isolation from tenants_for's Postgres round-trip."""
    active = next((t for t in tenants if t.tenant_uuid == active_tenant_uuid), None)
    return WhoamiResponse(user_id=sub, active=active, tenants=tenants)


@router.get("/v1/auth/whoami", response_model=WhoamiResponse)
def whoami(request: Request) -> WhoamiResponse:
    claims = getattr(request.state, "claims", None)
    if not claims or not claims.get("sub"):
        raise HTTPException(status_code=401, detail="missing or invalid token")
    reader = getattr(request.app.state, "whoami_reader", None)
    if reader is None:
        raise HTTPException(status_code=503, detail="whoami unavailable")
    return reader(claims["sub"], claims.get("tenant_id"))
