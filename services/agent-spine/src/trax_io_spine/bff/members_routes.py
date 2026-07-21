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

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from trax_io_spine.pg.members import AdminApiError, LastOwnerError, MemberNotFound

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
        if body.email in known_emails.values():
            raise HTTPException(status_code=409, detail="user already a member")

    try:
        user_id = admin_api.invite(body.email)
    except AdminApiError as exc:
        raise HTTPException(status_code=502, detail="identity provider error") from exc

    try:
        store.add(user_id=user_id, member_role=body.role, role=claims["tenant_role"])
    except LastOwnerError as exc:  # unreachable via InviteRole (excludes "owner") — defensive
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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
    # current tenant/role.
    claims = _claims(request)
    stores = request.app.state.members_stores
    if not stores:
        raise HTTPException(status_code=503, detail="members store unavailable")
    store = next(iter(stores.values()))
    store.set_preference(
        user_id=claims["sub"], target_tenant_uuid=body.tenant_id, role=claims["tenant_role"]
    )
    return Response(status_code=204)
