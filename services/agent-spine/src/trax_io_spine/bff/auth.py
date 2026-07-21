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
        return jwt.decode(token, self._secret, algorithms=["HS256"], audience=self._aud)


class JwksVerifier:
    def __init__(self, jwks_url: str, *, audience: str = "authenticated") -> None:
        self._client = PyJWKClient(jwks_url)
        self._aud = audience

    def _signing_key_for(self, token: str):
        return self._client.get_signing_key_from_jwt(token)

    def verify(self, token: str) -> dict:
        key = self._signing_key_for(token)
        return jwt.decode(
            token, key.key, algorithms=["ES256", "RS256"], audience=self._aud
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


class AuthMiddleware:
    """Pure ASGI middleware — no route changes; claims land in scope['state']."""

    def __init__(self, app, *, verifier: TokenVerifier,
                 tenant_uuids: dict[str, str] | None = None) -> None:
        self.app = app
        self.verifier = verifier
        self.tenant_uuids = tenant_uuids or {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1/tenants/"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return await _reject(401, "missing or invalid token")(scope, receive, send)
        try:
            claims = self.verifier.verify(auth[7:])
        except InvalidTokenError:
            return await _reject(401, "missing or invalid token")(scope, receive, send)
        if "tenant_id" not in claims:
            return await _reject(401, "missing or invalid token")(scope, receive, send)
        slug = scope["path"].split("/")[3]
        expected = self.tenant_uuids.get(slug)
        if expected is not None and claims["tenant_id"] != expected:
            return await _reject(403, "tenant mismatch")(scope, receive, send)
        scope.setdefault("state", {})["claims"] = claims
        return await self.app(scope, receive, send)
