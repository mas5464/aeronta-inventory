"""Tests for the TenantSpec registry and env-override loader."""
from __future__ import annotations

import json

import pytest

from observability_soc2.tenants import (
    LIGHTHOUSE_TENANTS,
    TenantSpec,
    load_tenants_from_env,
)

_ENV_OVERRIDE = "TRAX_IO_TENANTS_JSON"


# ---------------------------------------------------------------------- #
# TenantSpec validation                                                   #
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tenant_id",
    [
        "lighthouse-alpha",
        "abc",
        "tenant-b",
        "a00",
        "a" * 32,
        "a1b2c3-d4e5",
    ],
)
def test_tenant_spec_accepts_valid_ids(tenant_id: str) -> None:
    spec = TenantSpec(tenant_id=tenant_id, display_name="x", environment="dev")
    assert spec.tenant_id == tenant_id


@pytest.mark.parametrize(
    "tenant_id",
    [
        "Lighthouse",         # uppercase
        "LIGHTHOUSE",         # all uppercase
        "1abc",               # starts with digit
        "ab",                 # too short (2 chars)
        "a" * 33,             # too long (33 chars)
        "tenant_b",           # underscore forbidden
        "tenant b",           # space
        "-tenant",            # leading hyphen
        "",                   # empty
        "tenant.b",           # dot
    ],
)
def test_tenant_spec_rejects_invalid_ids(tenant_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        TenantSpec(tenant_id=tenant_id, display_name="x", environment="dev")


def test_tenant_spec_rejects_invalid_environment() -> None:
    with pytest.raises(ValueError, match="Invalid environment"):
        TenantSpec(
            tenant_id="abc",
            display_name="x",
            environment="production",  # type: ignore[arg-type]
        )


def test_tenant_spec_rejects_invalid_data_residency() -> None:
    with pytest.raises(ValueError, match="Invalid data_residency"):
        TenantSpec(
            tenant_id="abc",
            display_name="x",
            environment="dev",
            data_residency="ap-southeast-1",  # type: ignore[arg-type]
        )


def test_tenant_spec_is_frozen() -> None:
    spec = TenantSpec(tenant_id="abc", display_name="x", environment="dev")
    # frozen dataclass raises FrozenInstanceError (subclass of AttributeError)
    with pytest.raises(AttributeError):
        spec.tenant_id = "def"  # type: ignore[misc]


def test_lighthouse_tenants_ships_with_pilot() -> None:
    assert len(LIGHTHOUSE_TENANTS) >= 1
    ids = {t.tenant_id for t in LIGHTHOUSE_TENANTS}
    assert "lighthouse-alpha" in ids


# ---------------------------------------------------------------------- #
# load_tenants_from_env                                                   #
# ---------------------------------------------------------------------- #


def test_load_tenants_without_env_returns_lighthouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_ENV_OVERRIDE, raising=False)
    assert load_tenants_from_env() == LIGHTHOUSE_TENANTS


def test_load_tenants_with_empty_env_returns_lighthouse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV_OVERRIDE, "   ")
    assert load_tenants_from_env() == LIGHTHOUSE_TENANTS


def test_load_tenants_from_env_parses_json_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {
            "tenant_id": "tenant-one",
            "display_name": "Tenant One",
            "environment": "dev",
        },
        {
            "tenant_id": "tenant-two",
            "display_name": "Tenant Two",
            "environment": "prod",
            "data_residency": "eu-west-1",
        },
    ]
    monkeypatch.setenv(_ENV_OVERRIDE, json.dumps(payload))
    result = load_tenants_from_env()
    assert len(result) == 2
    assert result[0].tenant_id == "tenant-one"
    assert result[1].data_residency == "eu-west-1"
    assert result[1].environment == "prod"


def test_load_tenants_from_env_rejects_non_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_ENV_OVERRIDE, json.dumps({"tenant_id": "abc"}))
    with pytest.raises(ValueError, match="must decode to a JSON list"):
        load_tenants_from_env()


def test_load_tenants_from_env_propagates_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {"tenant_id": "BAD_ID", "display_name": "x", "environment": "dev"},
    ]
    monkeypatch.setenv(_ENV_OVERRIDE, json.dumps(payload))
    with pytest.raises(ValueError, match="Invalid tenant_id"):
        load_tenants_from_env()
