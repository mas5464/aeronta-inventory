"""Upload/ingest/poll routes for the self-serve CSV/Excel intake flow (C3 spec
§3, §6). Direct-to-Storage upload: the BFF only mints signed PUT URLs
(`POST .../uploads`) and enqueues a `jobs` row (`POST .../ingest`) that the
Railway worker (`pg.worker.HANDLERS["ingest"]` -> `pg.ingest.run_ingest`) picks
up — uploaded files never transit the BFF. `GET .../ingest/{job_id}` and
`GET .../ingest` poll/list that job's Postgres row through `IngestJobStore`
(`app.state.ingest_stores`, wired one-per-tenant exactly like
`members_routes.py`'s `members_stores`).

Auth: every route requires verified claims (401 without them, mirroring
`members_routes.py`'s `_claims` helper) — the C2 middleware's write-role floor
already 403s a `viewer` POST before a handler here even runs; GETs are readable
by any tenant member (no extra role check needed).
"""
from __future__ import annotations

import json
import uuid as _uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from trax_io_reco.ingest.canonical import CANONICAL_FILES, REQUIRED_FILES

from trax_io_spine.pg.uploads import UploadMintError

router = APIRouter()

INGEST_BASE = "/v1/tenants/{tenant_id}"


class MintUploadsRequest(BaseModel):
    files: list[str]


class CreateIngestRequest(BaseModel):
    batch_id: str
    files: dict[str, str]


def _claims(request: Request) -> dict:
    claims = getattr(request.state, "claims", None)
    if not claims:
        raise HTTPException(status_code=401, detail="auth required")
    return claims


def _store(request: Request, tenant_id: str):
    # INVARIANT (not enforced here): `ingest_stores` must only ever be
    # pre-warmed from this SAME app's `registry` resolution (see asgi.py's
    # DATABASE_URL boot) — never a second/independent source. `registry` is
    # also what the JWT middleware's tenant-slug match resolves through
    # (auth.py's tenant_uuid_for); if this dict ever diverged from it, the
    # middleware could authorize one tenant while this store layer silently
    # served another. Same invariant as app.py's `_store` and
    # members_routes.py's `_store` above; `_tenant_uuid` below shares it too.
    store = request.app.state.ingest_stores.get(tenant_id)
    if store is None:
        registry = getattr(request.app.state, "registry", None)
        if registry is not None:
            store = registry.ingest_store_for(tenant_id)
    if store is None:
        raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id}")
    return store


def _tenant_uuid(request: Request, tenant_id: str) -> str:
    # Same static-dict-then-registry invariant as `_store` above.
    uuid = request.app.state.tenant_uuids.get(tenant_id)
    if uuid is None:
        registry = getattr(request.app.state, "registry", None)
        if registry is not None:
            uuid = registry.uuid_for_slug(tenant_id)
    if uuid is None:
        raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id}")
    return uuid


@router.post(INGEST_BASE + "/uploads")
def mint_uploads(tenant_id: str, body: MintUploadsRequest, request: Request) -> dict:
    _claims(request)
    minter = request.app.state.upload_minter
    if minter is None:
        raise HTTPException(status_code=503, detail="upload minter unavailable")
    unknown = [name for name in body.files if name not in CANONICAL_FILES]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown file name(s): {unknown}")
    tenant_uuid = _tenant_uuid(request, tenant_id)
    batch_id = str(_uuid.uuid4())
    targets: dict[str, dict[str, str]] = {}
    try:
        for name in body.files:
            path = f"{tenant_uuid}/{batch_id}/{name}"
            targets[name] = {"url": minter.mint(path), "path": path}
    except UploadMintError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"batch_id": batch_id, "targets": targets}


@router.post(INGEST_BASE + "/ingest")
def create_ingest(tenant_id: str, body: CreateIngestRequest, request: Request) -> dict:
    claims = _claims(request)
    missing = [name for name in REQUIRED_FILES if name not in body.files]
    if missing:
        raise HTTPException(status_code=422, detail=f"missing required file(s): {missing}")
    tenant_uuid = _tenant_uuid(request, tenant_id)
    # Cross-tenant guard: validate all paths belong to this tenant's prefix
    for _name, path in body.files.items():
        segments = path.split("/")
        if segments[0] != tenant_uuid or ".." in segments or "" in segments:
            raise HTTPException(status_code=422, detail="file path outside tenant prefix")
    store = _store(request, tenant_id)
    payload = {
        "tenant_id": tenant_uuid,
        "tenant_slug": tenant_id,
        "batch_id": body.batch_id,
        "files": body.files,
        "uploaded_by": claims["sub"],
    }
    job_id = store.create(
        payload=payload, role=claims.get("tenant_role", "planner"), sub=claims["sub"]
    )
    return {"job_id": job_id}


def _parse_errors(error_text: str | None) -> list | None:
    if not error_text:
        return None
    try:
        return json.loads(error_text)
    except (json.JSONDecodeError, TypeError):
        return [error_text]


@router.get(INGEST_BASE + "/ingest/{job_id}")
def poll_ingest(tenant_id: str, job_id: int, request: Request) -> dict:
    claims = _claims(request)
    store = _store(request, tenant_id)
    job = store.get(job_id, role=claims.get("tenant_role", "planner"), sub=claims["sub"])
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return {
        "status": job["status"],
        "result": job["result"],
        "errors": _parse_errors(job["error"]),
    }


@router.get(INGEST_BASE + "/ingest")
def list_ingest(tenant_id: str, request: Request) -> list[dict]:
    claims = _claims(request)
    store = _store(request, tenant_id)
    return store.list_recent(role=claims.get("tenant_role", "planner"), sub=claims["sub"])
