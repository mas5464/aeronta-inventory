"""Native production composition: one config contract for serving and population."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("boto3")
pytest.importorskip("pyiceberg")

from trax_io_feature_store.online_writer import PopulateResult  # noqa: E402
from trax_io_feature_store.runtime import (  # noqa: E402
    NativeFeatureRuntime,
    build_native_runtime_from_env,
    populate_native_online_from_env,
)


class _DynamoResource:
    def __init__(self) -> None:
        self.requested_tables: list[str] = []
        self.table = object()

    def Table(self, name: str) -> object:  # noqa: N802 - boto3 API parity
        self.requested_tables.append(name)
        return self.table


def test_build_native_runtime_uses_tenant_scoped_defaults() -> None:
    catalog = object()
    dynamodb = _DynamoResource()

    runtime = build_native_runtime_from_env(
        "acme",
        environ={"TRAX_IO_FEATURE_ONLINE_TABLE": "acme-online"},
        catalog=catalog,
        dynamodb_resource=dynamodb,
    )

    assert isinstance(runtime, NativeFeatureRuntime)
    assert runtime.tenant.tenant_id == "acme"
    assert runtime.offline._catalog is catalog
    assert runtime.offline._identifier("stock_position", runtime.tenant) == (
        "trax_io_lake_acme.raw_stock_position"
    )
    assert runtime.online._table is dynamodb.table
    assert dynamodb.requested_tables == ["acme-online"]


def test_build_native_runtime_allows_explicit_local_namespace() -> None:
    runtime = build_native_runtime_from_env(
        "acme",
        environ={
            "TRAX_IO_FEATURE_ONLINE_TABLE": "online",
            "TRAX_IO_FEATURE_NAMESPACE": "trax_io",
            "TRAX_IO_FEATURE_TABLE_PREFIX": "",
        },
        catalog=object(),
        dynamodb_resource=_DynamoResource(),
    )

    assert runtime.offline._identifier("stock_position", runtime.tenant) == (
        "trax_io.stock_position"
    )


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"TRAX_IO_FEATURE_ONLINE_TABLE": ""},
        {"TRAX_IO_FEATURE_ONLINE_TABLE": "   "},
    ],
)
def test_build_native_runtime_requires_online_table(environ: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="TRAX_IO_FEATURE_ONLINE_TABLE is required"):
        build_native_runtime_from_env(
            "acme",
            environ=environ,
            catalog=object(),
            dynamodb_resource=_DynamoResource(),
        )


def test_native_population_uses_runtime_keyset_once(monkeypatch) -> None:
    keys = (("PN-A", "LOC-1"), ("PN-B", "LOC-2"))
    pinned_runtime = SimpleNamespace(
        tenant=SimpleNamespace(tenant_id="acme"),
        offline=object(),
        online=object(),
        keys=lambda: keys,
    )
    pin_calls = 0

    def pin_latest():
        nonlocal pin_calls
        pin_calls += 1
        return pinned_runtime

    runtime = SimpleNamespace(pin_latest=pin_latest)
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "trax_io_feature_store.runtime.build_native_runtime_from_env",
        lambda *args, **kwargs: runtime,
    )

    def _populate(offline, online, **kwargs):
        calls.append({"offline": offline, "online": online, **kwargs})
        return PopulateResult(written=2)

    monkeypatch.setattr("trax_io_feature_store.runtime.populate_online", _populate)

    result = populate_native_online_from_env(
        "acme",
        environ={"TRAX_IO_FEATURE_ONLINE_TABLE": "online"},
        demand_window=36,
    )

    assert result.keys == keys
    assert result.population == PopulateResult(written=2)
    assert pin_calls == 1
    assert calls == [
        {
            "offline": pinned_runtime.offline,
            "online": pinned_runtime.online,
            "tenant": pinned_runtime.tenant,
            "keys": keys,
            "demand_window": 36,
        }
    ]
