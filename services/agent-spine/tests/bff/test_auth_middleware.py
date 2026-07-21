"""JWT middleware: 401/403/dev-mode semantics against a real ES256 keypair.

No network: JwksVerifier is exercised via a monkeypatched PyJWKClient whose
signing key is generated in-test (cryptography is a pyjwt[crypto] dependency).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier, JwksVerifier, build_verifier_from_env
from trax_io_spine.bff.store import PlannerStore

TENANT_UUID = "753b64bd-9885-4639-b116-8f2c5c497232"

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture()
def store_factory():
    def _make() -> PlannerStore:
        return PlannerStore.from_extract(
            tenant_id="aeronta-demo", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
        )

    return _make


class _StaticVerifier:
    """TokenVerifier double for app-level tests: real HsVerifier below covers crypto."""

    def __init__(self, secret="unit-test-secret-0123456789abcdef"):
        self._v = HsVerifier(secret)

    def verify(self, token):
        return self._v.verify(token)


def _token(secret="unit-test-secret-0123456789abcdef", *, tenant=TENANT_UUID, role="planner", exp_min=5, aud="authenticated"):
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": "u1", "aud": aud, "iat": now, "exp": now + timedelta(minutes=exp_min),
         "tenant_id": tenant, "tenant_role": role},
        secret, algorithm="HS256",
    )


@pytest.fixture()
def client(store_factory):
    app = create_planner_app(
        {"aeronta-demo": store_factory()},
        verifier=_StaticVerifier(),
        tenant_uuids={"aeronta-demo": TENANT_UUID},
    )
    return TestClient(app)


def test_missing_token_401(client):
    assert client.get("/v1/tenants/aeronta-demo/recommendations").status_code == 401


def test_garbage_token_401(client):
    r = client.get(
        "/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": "Bearer not.a.jwt"},
    )
    assert r.status_code == 401


def test_expired_token_401(client):
    r = client.get(
        "/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": f"Bearer {_token(exp_min=-5)}"},
    )
    assert r.status_code == 401


def test_wrong_tenant_403(client):
    other_tenant = "99999999-9999-9999-9999-999999999999"
    r = client.get(
        "/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": f"Bearer {_token(tenant=other_tenant)}"},
    )
    assert r.status_code == 403


def test_valid_token_200(client):
    r = client.get(
        "/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert r.status_code == 200


def test_no_verifier_passthrough(store_factory):
    app = create_planner_app({"aeronta-demo": store_factory()})
    assert TestClient(app).get(
        "/v1/tenants/aeronta-demo/recommendations"
    ).status_code == 200


def test_build_verifier_from_env(monkeypatch, caplog):
    monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    import logging

    with caplog.at_level(logging.WARNING):
        assert build_verifier_from_env() is None
    assert any("AUTH DISABLED" in r.message for r in caplog.records)
    monkeypatch.setenv("AUTH_JWT_SECRET", "x")
    assert isinstance(build_verifier_from_env(), HsVerifier)


def test_jwks_verifier_es256(monkeypatch):
    key = generate_private_key(SECP256R1())
    tok = jwt.encode(
        {"sub": "u1", "aud": "authenticated", "tenant_id": TENANT_UUID,
         "exp": datetime.now(UTC) + timedelta(minutes=5)},
        key, algorithm="ES256", headers={"kid": "k1"},
    )
    v = JwksVerifier("https://example.invalid/jwks.json")

    class _FakeSigningKey:
        def __init__(self, k):
            self.key = k

    monkeypatch.setattr(
        v, "_signing_key_for", lambda token: _FakeSigningKey(key.public_key())
    )
    claims = v.verify(tok)
    assert claims["tenant_id"] == TENANT_UUID
    with pytest.raises(jwt.InvalidTokenError):
        v.verify(tok + "tamper")


def test_healthz_tokenless_on_verifier_enabled_app(client):
    r = client.get("/healthz")
    assert r.status_code == 200


def test_jwks_kid_miss_raises_invalid_token(monkeypatch):
    from jwt.exceptions import PyJWKClientError

    v = JwksVerifier("https://example.invalid/jwks.json")

    def _boom(token):
        raise PyJWKClientError("Unable to find a signing key that matches")

    monkeypatch.setattr(v, "_signing_key_for", _boom)
    with pytest.raises(jwt.InvalidTokenError):
        v.verify(_token())


def test_activate_tenant_path_verified_outside_tenant_scope(client):
    # /v1/auth/activate-tenant is deliberately OUTSIDE /v1/tenants/*, but the
    # middleware still verifies the token there (just skips the slug<->tenant_id
    # match, since there's no slug on this path). No token => 401.
    r = client.post("/v1/auth/activate-tenant", json={"tenant_id": TENANT_UUID})
    assert r.status_code == 401
    # A DIFFERENT tenant_id claim than any known slug must NOT 403 here (that
    # per-slug assertion only applies under /v1/tenants/{slug}/...) — with no
    # members_stores configured on this app, claims pass and the route 503s
    # instead (a members-layer concern, not an auth one).
    other_tenant = "99999999-9999-9999-9999-999999999999"
    r = client.post(
        "/v1/auth/activate-tenant",
        headers={"Authorization": f"Bearer {_token(tenant=other_tenant)}"},
        json={"tenant_id": TENANT_UUID},
    )
    assert r.status_code == 503


def test_middleware_returns_401_on_jwks_layer_failure(store_factory, monkeypatch):
    from jwt.exceptions import PyJWKClientError

    v = JwksVerifier("https://example.invalid/jwks.json")
    monkeypatch.setattr(
        v, "_signing_key_for",
        lambda token: (_ for _ in ()).throw(PyJWKClientError("no key")),
    )
    app = create_planner_app(
        {"aeronta-demo": store_factory()},
        verifier=v, tenant_uuids={"aeronta-demo": TENANT_UUID},
    )
    r = TestClient(app).get(
        "/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert r.status_code == 401
