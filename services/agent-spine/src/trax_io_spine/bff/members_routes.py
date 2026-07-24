"""Members management + tenant-activation routes (C2 spec §4).

Route-level gates (claims presence, admin/owner, owner-only for owner
grants/revokes) are a UX nicety, NOT the enforcement boundary — every write
still runs through `MembershipStore`, which threads the caller's VERIFIED
`tenant_role` into `tenant_conn` so Postgres RLS (migration 0006) is the real
authority. Members management NEVER runs in dev-trusted mode: with no
verifier configured, `request.state.claims` is never populated and every
route here 401s.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from trax_io_spine.pg.members import AdminApiError, LastOwnerError, MemberNotFound

logger = logging.getLogger(__name__)

router = APIRouter()

InviteRole = Literal["admin", "planner", "viewer"]
AnyRole = Literal["owner", "admin", "planner", "viewer"]

MEMBERS_BASE = "/v1/tenants/{tenant_id}/members"


class InviteRequest(BaseModel):
    email: str
    role: InviteRole


class UpdateRoleRequest(BaseModel):
    role: AnyRole


class ActivateTenantRequest(BaseModel):
    tenant_id: str


def _claims(request: Request) -> dict:
    claims = getattr(request.state, "claims", None)
    if not claims:
        raise HTTPException(status_code=401, detail="auth required")
    return claims


def _require_admin_or_owner(claims: dict) -> None:
    if claims.get("tenant_role") not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="admin or owner role required")


def _require_owner(claims: dict) -> None:
    if claims.get("tenant_role") != "owner":
        raise HTTPException(status_code=403, detail="owner role required")


def _store(request: Request, tenant_id: str):
    store = request.app.state.members_stores.get(tenant_id)
    if store is None:
        registry = getattr(request.app.state, "registry", None)
        if registry is not None:
            store = registry.members_store_for(tenant_id)
    if store is None:
        raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id}")
    return store


def _current_role_of(store, caller_role: str, user_id: str) -> str | None:
    for row in store.list(role=caller_role):
        if row["user_id"] == user_id:
            return row["role"]
    return None


def _enrich_emails(request: Request, rows: list[dict]) -> list[dict]:
    admin_api = request.app.state.admin_api
    if admin_api is None or not rows:
        return rows
    emails = admin_api.emails_for([r["user_id"] for r in rows])
    for row in rows:
        email = emails.get(row["user_id"])
        if email:
            row["email"] = email
    return rows


@router.get(MEMBERS_BASE)
def list_members(tenant_id: str, request: Request) -> list[dict]:
    claims = _claims(request)
    _require_admin_or_owner(claims)
    store = _store(request, tenant_id)
    rows = store.list(role=claims["tenant_role"])
    return _enrich_emails(request, rows)


@router.post(MEMBERS_BASE + "/invite")
def invite_member(tenant_id: str, body: InviteRequest, request: Request) -> dict:
    claims = _claims(request)
    _require_admin_or_owner(claims)
    store = _store(request, tenant_id)
    admin_api = request.app.state.admin_api
    if admin_api is None:
        raise HTTPException(status_code=502, detail="identity provider error")

    existing = store.list(role=claims["tenant_role"])
    if existing:
        known_emails = admin_api.emails_for([r["user_id"] for r in existing])
        wanted = body.email.lower()
        if wanted in {e.lower() for e in known_emails.values()}:
            raise HTTPException(status_code=409, detail="user already a member")

    try:
        user_id = admin_api.invite(body.email)
    except AdminApiError as exc:
        raise HTTPException(status_code=502, detail="identity provider error") from exc

    try:
        store.add(user_id=user_id, member_role=body.role, role=claims["tenant_role"])
    except LastOwnerError as exc:  # unreachable via InviteRole (excludes "owner") — defensive
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        # store.add failed after invite() already minted an auth user: best-effort
        # compensate so we don't leave an orphaned identity with no membership row.
        try:
            admin_api.delete_user(user_id)
        except Exception:  # compensation is best-effort, never masks the real error
            logger.exception(
                "failed to roll back invited user %s after membership creation error", user_id
            )
        raise HTTPException(
            status_code=500,
            detail="membership creation failed; invite rolled back",
        ) from exc

    return {"user_id": user_id, "role": body.role}


@router.patch(MEMBERS_BASE + "/{user_id}")
def update_member(
    tenant_id: str, user_id: str, body: UpdateRoleRequest, request: Request
) -> dict:
    claims = _claims(request)
    _require_admin_or_owner(claims)
    store = _store(request, tenant_id)
    current_role = _current_role_of(store, claims["tenant_role"], user_id)
    if body.role == "owner" or current_role == "owner":
        _require_owner(claims)
    try:
        store.update_role(user_id=user_id, member_role=body.role, role=claims["tenant_role"])
    except LastOwnerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MemberNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"user_id": user_id, "role": body.role}


@router.delete(MEMBERS_BASE + "/{user_id}")
def remove_member(tenant_id: str, user_id: str, request: Request) -> dict:
    claims = _claims(request)
    _require_admin_or_owner(claims)
    store = _store(request, tenant_id)
    current_role = _current_role_of(store, claims["tenant_role"], user_id)
    if current_role == "owner":
        _require_owner(claims)
    try:
        store.remove(user_id=user_id, role=claims["tenant_role"])
    except LastOwnerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MemberNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"removed": user_id}


@router.post("/v1/auth/activate-tenant", status_code=204)
def activate_tenant(body: ActivateTenantRequest, request: Request) -> Response:
    # Deliberately OUTSIDE /v1/tenants/{tenant_id}/... — that prefix's middleware
    # tenant-match assertion (claims.tenant_id == path tenant) would reject the
    # very request meant to switch AWAY from the caller's current tenant. Any
    # configured store works: tenant_preferences RLS gates on the JWT `sub`
    # only (see MembershipStore.set_preference), not on the connection's
    # current tenant/role — so this write is ALWAYS scoped to the caller's own
    # verified `sub` regardless of which store object services it, and that
    # exemption from the slug match stays safe under registry-backed
    # resolution for exactly the same reason.
    #
    # Store resolution, in order: a statically-configured store wins (every
    # dev/in-memory boot path, unchanged). Failing that, resolve a
    # MembershipStore off the CALLER'S OWN verified tenant uuid via
    # registry.members_store_for_uuid — NOT a "some/any tenant" lookup. That
    # method takes a uuid the caller already holds, does no slug lookup and no
    # database round trip, and therefore cannot fail to find a tenant (see its
    # docstring in tenant_registry.py). It replaces the former
    # any_members_store(), whose cold-cache "discover any tenant" fallback ran
    # an RLS-blocked bare-pool read (`tenants` is RLS-scoped and `trax_app` is
    # NOBYPASSRLS) and therefore 503'd on every real cold start — deleted in
    # Task 3's review, see tenant_registry.py's module docstring.
    claims = _claims(request)
    stores = request.app.state.members_stores
    store = next(iter(stores.values()), None)
    if store is None:
        registry = getattr(request.app.state, "registry", None)
        if registry is not None:
            store = registry.members_store_for_uuid(claims["tenant_id"])
    if store is None:
        raise HTTPException(status_code=503, detail="members store unavailable")
    store.set_preference(
        user_id=claims["sub"], target_tenant_uuid=body.tenant_id, role=claims["tenant_role"]
    )
    return Response(status_code=204)
