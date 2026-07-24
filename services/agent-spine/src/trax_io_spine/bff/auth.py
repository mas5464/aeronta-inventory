"""JWT verification for the BFF (C2 spec §3).

Verifier absent => DEV MODE: trusted path-param behavior, loud boot warning.
Verifier present => every /v1/tenants/{slug}/* request needs a valid Supabase
JWT whose verified tenant_id claim matches the slug's tenant uuid.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Protocol

import anyio
import jwt
from jwt import InvalidTokenError, PyJWKClient

log = logging.getLogger("trax_io_spine.bff.auth")


class TokenVerifier(Protocol):
    def verify(self, token: str) -> dict: ...


class HsVerifier:
    def __init__(self, secret: str, *, audience: str = "authenticated") -> None:
        self._secret = secret
        self._aud = audience

    def verify(self, token: str) -> dict:
        return jwt.decode(
            token, self._secret, algorithms=["HS256"], audience=self._aud,
            options={"require": ["exp"]},
        )


class JwksVerifier:
    def __init__(self, jwks_url: str, *, audience: str = "authenticated") -> None:
        self._client = PyJWKClient(jwks_url)
        self._aud = audience

    def _signing_key_for(self, token: str):
        return self._client.get_signing_key_from_jwt(token)

    def verify(self, token: str) -> dict:
        try:
            key = self._signing_key_for(token)
        except jwt.PyJWTError as exc:  # PyJWKClientError/ConnectionError inherit PyJWTError
            raise InvalidTokenError(str(exc)) from exc
        return jwt.decode(
            token, key.key, algorithms=["ES256", "RS256"], audience=self._aud,
            options={"require": ["exp"]},
        )


def build_verifier_from_env() -> TokenVerifier | None:
    aud = os.environ.get("AUTH_AUDIENCE", "authenticated")
    jwks = os.environ.get("AUTH_JWKS_URL")
    if jwks:
        return JwksVerifier(jwks, audience=aud)
    secret = os.environ.get("AUTH_JWT_SECRET")
    if secret:
        return HsVerifier(secret, audience=aud)
    log.warning("AUTH DISABLED — trusted path-param mode (dev only)")
    return None


def _reject(status: int, detail: str):
    async def responder(scope, receive, send):
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})

    return responder


# Outside /v1/tenants/* but still needs verified claims: the tenant-switching
# route (bff/members_routes.py) reads request.state.claims to identify the
# caller, and deliberately lives outside /v1/tenants/{tenant_id}/... so the
# per-slug tenant-match assertion below doesn't reject the very request meant
# to switch to a DIFFERENT tenant.
_UNSCOPED_AUTHED_PATHS = frozenset({"/v1/auth/activate-tenant"})

# C4 billing write-gate: subscription statuses that still permit writes.
# "past_due" is included deliberately — Stripe keeps retrying payment for a
# grace period before the subscription lapses to "canceled"/"unpaid", and we
# don't want to lock a tenant out on the first missed charge.
_ACTIVE_SUBSCRIPTION_STATUSES = frozenset({"trialing", "active", "past_due"})


class AuthMiddleware:
    """Pure ASGI middleware — no route changes; claims land in scope['state']."""

    def __init__(self, app, *, verifier: TokenVerifier,
                 tenant_uuids: dict[str, str] | None = None,
                 subscription_status_for=None) -> None:
        self.app = app
        self.verifier = verifier
        self.tenant_uuids = tenant_uuids or {}
        # Callable[[str], str | None] | None — maps a tenant uuid to its
        # subscription status. None (the default) means "no gate" (dev/
        # in-memory boot paths never pass this): behavior is unchanged.
        self.subscription_status_for = subscription_status_for

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        is_tenant_scoped = path.startswith("/v1/tenants/")
        is_unscoped_authed = path in _UNSCOPED_AUTHED_PATHS
        if scope["type"] != "http" or not (is_tenant_scoped or is_unscoped_authed):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return await _reject(401, "missing or invalid token")(scope, receive, send)
        try:
            claims = self.verifier.verify(auth[7:])
        except jwt.PyJWTError:
            return await _reject(401, "missing or invalid token")(scope, receive, send)
        if "tenant_id" not in claims:
            return await _reject(401, "missing or invalid token")(scope, receive, send)
        if is_tenant_scoped:
            slug = path.split("/")[3]
            expected = self.tenant_uuids.get(slug)
            if expected is not None and claims["tenant_id"] != expected:
                return await _reject(403, "tenant mismatch")(scope, receive, send)
            # Write-method role floor: viewer-and-below may read but never write.
            # Members routes layer a stricter admin/owner check on top of this.
            method = scope.get("method", "GET")
            if method not in ("GET", "HEAD", "OPTIONS") and claims.get(
                "tenant_role"
            ) not in ("planner", "admin", "owner"):
                return await _reject(403, "insufficient role")(scope, receive, send)
            # Billing write-gate (C4): reads are never gated. When a
            # subscription_status_for callable is configured, writes are
            # blocked with 402 unless the tenant's subscription is in an
            # active-ish state. None (no callable) ⇒ no gating (dev/
            # in-memory boot paths never pass this — behavior unchanged).
            # `expected is None` means the slug isn't in tenant_uuids — can't
            # happen in current wiring (create_planner_app always seeds
            # tenant_uuids for every configured tenant), but skip the gate
            # rather than pass None into a callable that expects a uuid.
            gate = self.subscription_status_for
            if (
                method not in ("GET", "HEAD", "OPTIONS")
                and gate is not None
                and expected is not None
            ):
                try:
                    # gate() does a sync psycopg pool read — offload to a
                    # thread so we don't block the event loop for the
                    # duration of the DB round-trip.
                    status = await anyio.to_thread.run_sync(gate, expected)
                except Exception:
                    log.exception("subscription status read failed for tenant %s", expected)
                    return await _reject(
                        503, "subscription status unavailable"
                    )(scope, receive, send)
                if status not in _ACTIVE_SUBSCRIPTION_STATUSES:
                    return await _reject(402, "subscription inactive")(scope, receive, send)
        scope.setdefault("state", {})["claims"] = claims
        return await self.app(scope, receive, send)
