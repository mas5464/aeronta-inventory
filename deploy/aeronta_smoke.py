"""Live smoke test for Aeronta Inventory's Supabase auth activation (C2 Task 9),
plus an optional C3 Task 7 self-serve ingest stage.

Env-gated so it is safe to leave wired into scripts/CI without a live
environment: missing required env prints ``SKIP (env unset)`` and exits 0.

Required env:
  AERONTA_SMOKE_EMAIL, AERONTA_SMOKE_PASSWORD, AERONTA_ANON_KEY

Optional env:
  AERONTA_SUPABASE_URL  (default https://sluoxufnqwusmtckklnv.supabase.co)
  AERONTA_BFF_URL       (if set, also exercises the BFF auth wiring, and is
                         required — together with AERONTA_SMOKE_INGEST=1 — for
                         the ingest stage below)
  AERONTA_SMOKE_INGEST  (set to "1" to also run the C3 upload/ingest/poll
                         flow against AERONTA_BFF_URL — see stage 4 below)

What it proves: a real password-grant sign-in against live Supabase mints an
access token carrying ``tenant_id``/``tenant_role`` claims — i.e. the
``public.custom_access_token_hook`` registered via the Management API
actually fires for ``supabase_auth_admin`` (migration 0007's grants). That
claims assertion is the load-bearing check of the whole C2 auth design; the
optional BFF checks are a secondary, best-effort layer on top.

Stage 4 (C3 Task 7, additive — only runs when both AERONTA_SMOKE_INGEST=1 and
AERONTA_BFF_URL are set) proves the self-serve upload -> ingest -> poll flow
end to end against a live deploy: mint signed upload URLs for the tiny
canonical batch in deploy/sample_upload/, PUT each CSV straight to Storage,
create an ingest job with the MINTED paths (never self-constructed — the
ingest route 422s any path outside the ``{tenant_uuid}/`` prefix it handed
back), then poll until the job reaches a terminal state. NOTE: this replaces
aeronta-demo's seeded data with the sample batch (see the Task 7 brief) —
acceptable for the demo tenant.

Usage (from services/agent-spine):
  uv run --extra bff python ../../deploy/aeronta_smoke.py
or with any interpreter that has httpx on the path.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx

DEMO_TENANT = "aeronta-demo"
DEFAULT_SUPABASE_URL = "https://sluoxufnqwusmtckklnv.supabase.co"

SAMPLE_UPLOAD_DIR = Path(__file__).resolve().parent / "sample_upload"
SAMPLE_FILES = ("parts", "stock", "demand_history")

INGEST_POLL_TIMEOUT_S = 60.0
INGEST_POLL_INTERVAL_S = 2.0


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded)


def _decode_claims(access_token: str) -> dict:
    parts = access_token.split(".")
    if len(parts) != 3:
        _fail(f"access token is not a 3-part JWT (got {len(parts)} segment(s))")
    return json.loads(_b64url_decode(parts[1]))


def _run_ingest_stage(base: str, auth_headers: dict) -> None:
    """C3 Task 7: upload -> ingest -> poll against a live BFF deploy.

    Mints signed upload URLs for the tiny canonical batch in
    deploy/sample_upload/, PUTs each CSV straight to the returned (unauthed)
    signed URL, creates an ingest job using the MINTED ``path`` values (never
    self-constructed — the route 422s any path outside the tenant's prefix),
    then polls until the job reaches a terminal state.
    """
    sample_bytes = {
        name: (SAMPLE_UPLOAD_DIR / f"{name}.csv").read_bytes() for name in SAMPLE_FILES
    }

    # 1. Mint signed upload URLs.
    mint_resp = httpx.post(
        f"{base}/uploads",
        json={"files": list(SAMPLE_FILES)},
        headers=auth_headers,
        timeout=15.0,
    )
    if mint_resp.status_code != 200:
        _fail(f"ingest: mint uploads returned {mint_resp.status_code}: {mint_resp.text[:200]}")
    mint_body = mint_resp.json()
    batch_id = mint_body.get("batch_id")
    targets = mint_body.get("targets") or {}
    missing_targets = [name for name in SAMPLE_FILES if name not in targets]
    if not batch_id or missing_targets:
        _fail(
            f"ingest: mint uploads response missing batch_id and/or targets "
            f"for {missing_targets} (body: {mint_body})"
        )

    # 2. PUT each sample CSV to its signed url (plain PUT, no auth).
    for name in SAMPLE_FILES:
        target_url = targets[name]["url"]
        put_resp = httpx.put(target_url, content=sample_bytes[name], timeout=30.0)
        if put_resp.status_code // 100 != 2:
            _fail(
                f"ingest: PUT {name}.csv returned {put_resp.status_code}: "
                f"{put_resp.text[:200]}"
            )

    # 3. Create the ingest job using the MINTED paths (not self-constructed).
    create_resp = httpx.post(
        f"{base}/ingest",
        json={
            "batch_id": batch_id,
            "files": {name: targets[name]["path"] for name in SAMPLE_FILES},
        },
        headers=auth_headers,
        timeout=15.0,
    )
    if create_resp.status_code != 200:
        _fail(f"ingest: create ingest returned {create_resp.status_code}: {create_resp.text[:200]}")
    job_id = create_resp.json().get("job_id")
    if job_id is None:
        _fail("ingest: create ingest response had no job_id")

    # 4. Poll until terminal (done/failed), bounded by a timeout.
    job: dict = {}
    deadline = time.monotonic() + INGEST_POLL_TIMEOUT_S
    while True:
        poll_resp = httpx.get(f"{base}/ingest/{job_id}", headers=auth_headers, timeout=15.0)
        if poll_resp.status_code != 200:
            _fail(
                f"ingest: poll job {job_id} returned {poll_resp.status_code}: "
                f"{poll_resp.text[:200]}"
            )
        job = poll_resp.json()
        if job.get("status") in ("done", "failed"):
            break
        if time.monotonic() >= deadline:
            _fail(
                f"ingest: job {job_id} still {job.get('status')!r} after "
                f"{INGEST_POLL_TIMEOUT_S:.0f}s (timed out waiting for done/failed)"
            )
        time.sleep(INGEST_POLL_INTERVAL_S)

    # 5. Assert success.
    if job["status"] != "done":
        _fail(f"ingest: job {job_id} failed: {job.get('errors')}")
    result = job.get("result") or {}
    keys = result.get("keys")
    if not isinstance(keys, int) or keys < 1:
        _fail(f"ingest: job {job_id} done but result.keys={keys!r}, expected >= 1")

    recs = result.get("recommendations")
    print(f"ingest OK · job {job['status']} · keys={keys} recs={recs}")


def main() -> None:
    email = os.environ.get("AERONTA_SMOKE_EMAIL")
    password = os.environ.get("AERONTA_SMOKE_PASSWORD")
    anon_key = os.environ.get("AERONTA_ANON_KEY")
    supabase_url = os.environ.get("AERONTA_SUPABASE_URL", DEFAULT_SUPABASE_URL)
    bff_url = os.environ.get("AERONTA_BFF_URL")
    smoke_ingest = os.environ.get("AERONTA_SMOKE_INGEST") == "1"

    if not email or not password or not anon_key:
        print("SKIP (env unset)")
        sys.exit(0)

    # 1. Password-grant sign-in.
    resp = httpx.post(
        f"{supabase_url}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": anon_key},
        json={"email": email, "password": password},
        timeout=15.0,
    )
    if resp.status_code != 200:
        _fail(f"sign-in returned {resp.status_code}: {resp.text[:200]}")
    access_token = resp.json().get("access_token")
    if not access_token:
        _fail("sign-in response had no access_token")

    # 2. Claims-stage assertion — THE load-bearing check: proves the custom
    #    access token hook fired (registered via the Management API against
    #    the grants in migration 0007) and minted tenant claims on a real login.
    claims = _decode_claims(access_token)
    tenant_id = claims.get("tenant_id")
    tenant_role = claims.get("tenant_role")
    if not tenant_id or not tenant_role:
        _fail(
            "minted JWT is missing tenant_id/tenant_role claims — the custom "
            "access token hook did not fire (check Management API hook "
            "registration: hook_custom_access_token_enabled / "
            "hook_custom_access_token_uri, and migration 0007's grants)"
        )
    summary = f"sign-in OK · claims: tenant_id={tenant_id} tenant_role={tenant_role}"

    # 3. Optional BFF checks (skipped until the BFF is deployed, C2 Task 11).
    if not bff_url:
        print(f"{summary} · BFF checks skipped (no AERONTA_BFF_URL)")
        return

    base = f"{bff_url}/v1/tenants/{DEMO_TENANT}"
    auth_headers = {"Authorization": f"Bearer {access_token}"}

    authed = httpx.get(f"{base}/recommendations", headers=auth_headers, timeout=15.0)
    if authed.status_code != 200:
        _fail(f"BFF recommendations (authed) returned {authed.status_code}, expected 200")
    if not authed.json().get("items"):
        _fail("BFF recommendations (authed) returned 200 but no rows")

    unauthed = httpx.get(f"{base}/recommendations", timeout=15.0)
    if unauthed.status_code != 401:
        _fail(f"BFF recommendations (no auth) returned {unauthed.status_code}, expected 401")

    members = httpx.get(f"{base}/members", headers=auth_headers, timeout=15.0)
    expect_members = 200 if tenant_role in ("admin", "owner") else 403
    if members.status_code != expect_members:
        _fail(
            f"BFF members (role={tenant_role}) returned {members.status_code}, "
            f"expected {expect_members}"
        )

    print(
        f"{summary} · BFF checks: recommendations 200/401 OK, "
        f"members {members.status_code} OK (role={tenant_role})"
    )

    # 4. Optional C3 Task 7 ingest stage (skipped unless both AERONTA_SMOKE_INGEST=1
    #    and AERONTA_BFF_URL are set — additive on top of the C2 stages above).
    if not smoke_ingest:
        return
    _run_ingest_stage(base, auth_headers)


if __name__ == "__main__":
    main()
