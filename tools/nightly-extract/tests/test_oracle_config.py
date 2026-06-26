"""Tests for OracleConnectionConfig env-var parsing and safe repr."""

from __future__ import annotations

import pytest

from trax_io_extract.oracle import (
    MissingOracleConfigError,
    OracleConnectionConfig,
    _safe_repr,
)


def _complete_env() -> dict[str, str]:
    return {
        "TRAX_ORACLE_HOST": "db.example.com",
        "TRAX_ORACLE_PORT": "1522",
        "TRAX_ORACLE_SERVICE": "EMRO",
        "TRAX_ORACLE_USER": "trax_reader",
        "TRAX_ORACLE_PASSWORD": "s3cret!",
        "TRAX_ORACLE_WALLET": "/opt/wallet",
    }


def test_from_env_happy_path() -> None:
    cfg = OracleConnectionConfig.from_env(_complete_env())
    assert cfg.host == "db.example.com"
    assert cfg.port == 1522
    assert cfg.service_name == "EMRO"
    assert cfg.user == "trax_reader"
    assert cfg.password == "s3cret!"
    assert cfg.wallet_location == "/opt/wallet"


def test_from_env_port_defaults_to_1521() -> None:
    env = _complete_env()
    env.pop("TRAX_ORACLE_PORT")
    cfg = OracleConnectionConfig.from_env(env)
    assert cfg.port == 1521


def test_from_env_wallet_optional() -> None:
    env = _complete_env()
    env.pop("TRAX_ORACLE_WALLET")
    cfg = OracleConnectionConfig.from_env(env)
    assert cfg.wallet_location is None


def test_from_env_missing_required_raises() -> None:
    env = _complete_env()
    env.pop("TRAX_ORACLE_HOST")
    env.pop("TRAX_ORACLE_USER")
    with pytest.raises(MissingOracleConfigError) as ei:
        OracleConnectionConfig.from_env(env)
    missing = ei.value.missing
    assert "TRAX_ORACLE_HOST" in missing
    assert "TRAX_ORACLE_USER" in missing


def test_from_env_empty_string_counts_as_missing() -> None:
    env = _complete_env()
    env["TRAX_ORACLE_PASSWORD"] = ""
    with pytest.raises(MissingOracleConfigError):
        OracleConnectionConfig.from_env(env)


def test_from_env_invalid_port_raises() -> None:
    env = _complete_env()
    env["TRAX_ORACLE_PORT"] = "not-a-number"
    with pytest.raises(MissingOracleConfigError):
        OracleConnectionConfig.from_env(env)


def test_safe_repr_redacts_password_and_wallet() -> None:
    cfg = OracleConnectionConfig.from_env(_complete_env())
    s = _safe_repr(cfg)
    assert "s3cret!" not in s
    assert "/opt/wallet" not in s
    assert "'***'" in s
    # Non-sensitive fields are still present.
    assert "db.example.com" in s
    assert "trax_reader" in s


def test_safe_repr_no_wallet_stays_none() -> None:
    env = _complete_env()
    env.pop("TRAX_ORACLE_WALLET")
    cfg = OracleConnectionConfig.from_env(env)
    s = _safe_repr(cfg)
    assert "wallet_location=None" in s
    assert "s3cret!" not in s
