"""Signed upload URLs for tenant-scoped Storage uploads (C3 spec §3/§6), plus the
Postgres-backed ingest-jobs store the BFF routes (`bff/ingest_routes.py`) use to
create/poll/list `jobs` rows. Two concerns, one module: both back the
upload -> ingest -> poll flow and there is no other new pg-layer file for C3
Task 5.

`SignedUrlMinter` is the protocol seam (mirrors `pg.ingest.StorageReader` /
`pg.members.AdminApi`) so route tests fake Storage instead of hitting it live —
see `tests/pg/test_c3_ingest_routes.py`'s `FakeMinter`. `IngestJobStore` mirrors
`pg.members.MembershipStore`'s shape (pool + tenant_uuid, wired one-per-tenant
onto `app.state.ingest_stores` exactly like `members_routes.py`'s
`members_stores`) so every read/write goes through `tenant_conn` and Postgres
RLS (migration 0006's `jobs_select`/`jobs_insert` policies) stays the real
authority, not app code.
"""
from __future__ import annotations

import json
from typing import Protocol

import httpx

from .db import tenant_conn


class UploadMintError(RuntimeError):
    """Raised when Storage's signed-upload-URL endpoint returns a non-2xx response."""


class SignedUrlMinter(Protocol):
    def mint(self, path: str) -> str: ...


class HttpxSignedUrlMinter:
    """Mints a signed PUT URL via Supabase Storage's signed-upload endpoint.

    Matches storage-js's `createSignedUploadUrl`: POSTs to
    `{supabase_url}/storage/v1/object/upload/sign/{bucket}/{path}` with the
    service key. The response's `url` field is a RELATIVE path (e.g.
    `/object/upload/sign/{bucket}/{path}?token=...`) that must be re-based onto
    `{supabase_url}/storage/v1` — it is not a usable URL as-is.
    """

    def __init__(
        self, supabase_url: str, service_key: str, bucket: str = "tenant-uploads"
    ) -> None:
        self._base = supabase_url.rstrip("/")
        self._key = service_key
        self._bucket = bucket

    def mint(self, path: str) -> str:
        url = f"{self._base}/storage/v1/object/upload/sign/{self._bucket}/{path}"
        headers = {"Authorization": f"Bearer {self._key}", "apikey": self._key}
        try:
            resp = httpx.post(url, headers=headers, timeout=15)
        except httpx.HTTPError as exc:
            raise UploadMintError(str(exc)) from exc
        if resp.status_code // 100 != 2:
            raise UploadMintError(
                f"mint failed for {path!r}: {resp.status_code} {resp.text}"
            )
        token_path = resp.json()["url"]
        return f"{self._base}/storage/v1{token_path}"


class IngestJobStore:
    """Per-tenant `jobs` CRUD for the ingest BFF routes — mirrors
    `pg.members.MembershipStore`'s pool+tenant_uuid shape and `tenant_conn` usage,
    so RLS (not app code) is the write/read authority."""

    def __init__(self, pool, *, tenant_uuid: str) -> None:
        self._pool = pool
        self._uuid = tenant_uuid

    def create(self, *, payload: dict, role: str, sub: str, kind: str = "ingest") -> int:
        with tenant_conn(self._pool, tenant_uuid=self._uuid, role=role, sub=sub) as conn:
            row = conn.execute(
                "insert into jobs (tenant_id, kind, payload) "
                "values (%s::uuid, %s, %s) returning id",
                (self._uuid, kind, json.dumps(payload)),
            ).fetchone()
            return row[0]

    def get(self, job_id: int, *, role: str, sub: str) -> dict | None:
        with tenant_conn(self._pool, tenant_uuid=self._uuid, role=role, sub=sub) as conn:
            row = conn.execute(
                "select id, status, result, error, payload, created_at "
                "from jobs where id = %s and tenant_id = %s::uuid",
                (job_id, self._uuid),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "status": row[1],
            "result": row[2],
            "error": row[3],
            "payload": row[4],
            "created_at": row[5].isoformat(),
        }

    def list_recent(
        self, *, role: str, sub: str, kind: str = "ingest", limit: int = 20
    ) -> list[dict]:
        with tenant_conn(self._pool, tenant_uuid=self._uuid, role=role, sub=sub) as conn:
            rows = conn.execute(
                "select id, status, result, payload, created_at from jobs "
                "where tenant_id = %s::uuid and kind = %s "
                "order by created_at desc limit %s",
                (self._uuid, kind, limit),
            ).fetchall()
        return [
            {
                "id": r[0],
                "status": r[1],
                "result": r[2],
                "uploaded_by": (r[3] or {}).get("uploaded_by"),
                "created_at": r[4].isoformat(),
            }
            for r in rows
        ]
