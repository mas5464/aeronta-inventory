"""Live smoke test for Aeronta Inventory's Supabase auth activation (C2 Task 9).

Env-gated so it is safe to leave wired into scripts/CI without a live
environment: missing required env prints ``SKIP (env unset)`` and exits 0.

Required env:
  AERONTA_SMOKE_EMAIL, AERONTA_SMOKE_PASSWORD, AERONTA_ANON_KEY

Optional env:
  AERONTA_SUPABASE_URL  (default https://sluoxufnqwusmtckklnv.supabase.co)
  AERONTA_BFF_URL       (if set, also exercises the BFF auth wiring)

What it proves: a real password-grant sign-in against live Supabase mints an
access token carrying ``tenant_id``/``tenant_role`` claims — i.e. the
``public.custom_access_token_hook`` registered via the Management API
actually fires for ``supabase_auth_admin`` (migration 0007's grants). That
claims assertion is the load-bearing check of the whole C2 auth design; the
optional BFF checks are a secondary, best-effort layer on top.

Usage (from services/agent-spine):
  uv run --extra bff python ../../deploy/aeronta_smoke.py
or with any interpreter that has httpx on the path.
"""

from __future__ import annotations

import base64
import json
import os
import sys

import httpx

DEMO_TENANT = "aeronta-demo"
DEFAULT_SUPABASE_URL = "https://sluoxufnqwusmtckklnv.supabase.co"


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


def main() -> None:
    email = os.environ.get("AERONTA_SMOKE_EMAIL")
    password = os.environ.get("AERONTA_SMOKE_PASSWORD")
    anon_key = os.environ.get("AERONTA_ANON_KEY")
    supabase_url = os.environ.get("AERONTA_SUPABASE_URL", DEFAULT_SUPABASE_URL)
    bff_url = os.environ.get("AERONTA_BFF_URL")

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


if __name__ == "__main__":
    main()
