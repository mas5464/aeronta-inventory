"""Tenant membership management (C2 spec §4). RLS does the enforcement: every
call runs under tenant_conn with the CALLER'S verified role — a planner-role
claim cannot write memberships no matter what this code does."""
from __future__ import annotations

from typing import Protocol

import httpx

from .db import tenant_conn


class LastOwnerError(Exception):
    pass


class MemberNotFound(Exception):  # noqa: N818 — matches the C2 spec's Interfaces block
    pass


class AdminApiError(Exception):
    pass


class MembershipStore:
    def __init__(self, pool, *, tenant_uuid: str) -> None:
        self._pool = pool
        self._uuid = tenant_uuid

    def _conn(self, role: str, *, sub: str | None = None):
        return tenant_conn(self._pool, tenant_uuid=self._uuid, role=role, sub=sub)

    def list(self, *, role: str) -> list[dict]:
        with self._conn(role) as conn:
            rows = conn.execute(
                "select user_id::text, role, created_at from memberships "
                "where tenant_id = %s::uuid order by created_at",
                (self._uuid,),
            ).fetchall()
            return [
                {"user_id": r[0], "role": r[1], "created_at": r[2].isoformat()}
                for r in rows
            ]

    def add(self, *, user_id: str, member_role: str, role: str) -> None:
        with self._conn(role) as conn:
            conn.execute(
                "insert into memberships (user_id, tenant_id, role) "
                "values (%s::uuid, %s::uuid, %s)",
                (user_id, self._uuid, member_role),
            )

    def _guard_last_owner(self, conn, user_id: str) -> None:
        target = conn.execute(
            "select role from memberships where tenant_id = %s::uuid "
            "and user_id = %s::uuid for update",
            (self._uuid, user_id),
        ).fetchone()
        if target is None:
            raise MemberNotFound(user_id)
        if target[0] == "owner":
            owners = conn.execute(
                "select count(*) from memberships "
                "where tenant_id = %s::uuid and role = 'owner'",
                (self._uuid,),
            ).fetchone()[0]
            if owners <= 1:
                raise LastOwnerError(user_id)

    def update_role(self, *, user_id: str, member_role: str, role: str) -> None:
        with self._conn(role) as conn:
            self._guard_last_owner(conn, user_id)
            conn.execute(
                "update memberships set role = %s "
                "where tenant_id = %s::uuid and user_id = %s::uuid",
                (member_role, self._uuid, user_id),
            )

    def remove(self, *, user_id: str, role: str) -> None:
        with self._conn(role) as conn:
            self._guard_last_owner(conn, user_id)
            conn.execute(
                "delete from memberships where tenant_id = %s::uuid and user_id = %s::uuid",
                (self._uuid, user_id),
            )

    def set_preference(self, *, user_id: str, target_tenant_uuid: str, role: str) -> None:
        # Tenant switching (C2 addendum): tenant_preferences RLS gates on the
        # JWT `sub` only (see migration 0006 tenant_preferences_own policy) — it
        # doesn't care which tenant/role this connection is otherwise scoped to,
        # which is why ANY configured MembershipStore can write it (the hook
        # validates the target's real membership at the NEXT mint, so a foreign
        # or stale preference is simply inert, never a privilege escalation).
        with self._conn(role, sub=user_id) as conn:
            conn.execute(
                "insert into tenant_preferences (user_id, tenant_id) "
                "values (%s::uuid, %s::uuid) "
                "on conflict (user_id) do update "
                "set tenant_id = excluded.tenant_id, updated_at = now()",
                (user_id, target_tenant_uuid),
            )


class AdminApi(Protocol):
    def invite(self, email: str) -> str: ...

    def emails_for(self, user_ids: list[str]) -> dict[str, str]: ...


class HttpxAdminApi:
    def __init__(self, supabase_url: str, service_key: str) -> None:
        self._base = supabase_url.rstrip("/")
        self._headers = {
            "apikey": service_key, "Authorization": f"Bearer {service_key}"
        }

    def invite(self, email: str) -> str:
        r = httpx.post(
            f"{self._base}/auth/v1/invite", headers=self._headers,
            json={"email": email}, timeout=15,
        )
        if r.status_code >= 300:
            raise AdminApiError(r.status_code)
        return r.json()["id"]

    def emails_for(self, user_ids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for uid in user_ids:
            r = httpx.get(
                f"{self._base}/auth/v1/admin/users/{uid}",
                headers=self._headers, timeout=15,
            )
            if r.status_code < 300:
                out[uid] = r.json().get("email", "")
        return out
