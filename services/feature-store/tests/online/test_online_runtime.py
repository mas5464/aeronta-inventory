"""Serving composition stays Dynamo-only and never imports the offline lake."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("boto3")

from trax_io_feature_store.online_runtime import (  # noqa: E402
    NativeOnlineRuntime,
    NativeOnlineSnapshot,
    build_native_online_runtime_from_env,
)
from trax_io_feature_store.online_store import (  # noqa: E402
    OnlineGeneration,
    _bundle_sort_key,
)


class _OnlineTable:
    def get_item(self, **_kwargs):
        return {
            "Item": {
                "tenant_id": "acme",
                "pn_location": "_meta#population",
                "generation": "generation-1",
                "key_count": 1,
            }
        }

    def query(self, **_kwargs):
        return {
            "Items": [
                {
                    "pn_location": _bundle_sort_key(
                        "generation-1",
                        "PN-A",
                        "LOC-1",
                    )
                }
            ]
        }


class _DynamoResource:
    def __init__(self) -> None:
        self.requested_tables: list[str] = []
        self.table = _OnlineTable()

    def Table(self, name: str) -> _OnlineTable:  # noqa: N802 - boto3 API parity
        self.requested_tables.append(name)
        return self.table


def test_serving_runtime_is_online_only() -> None:
    dynamodb = _DynamoResource()

    runtime = build_native_online_runtime_from_env(
        "acme",
        environ={"TRAX_IO_FEATURE_ONLINE_TABLE": "acme-online"},
        dynamodb_resource=dynamodb,
    )

    assert isinstance(runtime, NativeOnlineRuntime)
    assert runtime.tenant.tenant_id == "acme"
    assert runtime.snapshot() == NativeOnlineSnapshot(
        generation=OnlineGeneration(
            tenant_id="acme",
            generation="generation-1",
            key_count=1,
        ),
        keys=(("PN-A", "LOC-1"),),
    )
    assert dynamodb.requested_tables == ["acme-online"]


def test_snapshot_pins_one_generation_for_key_query() -> None:
    generation = OnlineGeneration(
        tenant_id="acme",
        generation="generation-1",
        key_count=1,
    )

    class Online:
        def __init__(self) -> None:
            self.received = []

        def current_generation(self, *, tenant):
            assert tenant.tenant_id == "acme"
            return generation

        def iter_keys(self, *, tenant, generation):
            self.received.append((tenant, generation))
            return (("PN-A", "LOC-1"),)

    online = Online()
    runtime = NativeOnlineRuntime(
        tenant=type("Tenant", (), {"tenant_id": "acme"})(),
        online=online,
    )

    snapshot = runtime.snapshot()

    assert snapshot.generation is generation
    assert snapshot.keys == (("PN-A", "LOC-1"),)
    assert online.received == [(runtime.tenant, generation)]


def test_serving_runtime_module_imports_with_pyiceberg_blocked() -> None:
    script = """
import importlib.abc
import sys

class BlockPyIceberg(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pyiceberg" or fullname.startswith("pyiceberg."):
            raise ModuleNotFoundError("pyiceberg intentionally absent from serving")
        return None

sys.meta_path.insert(0, BlockPyIceberg())
from trax_io_feature_store.online_runtime import build_native_online_runtime_from_env
assert not any(
    name == "pyiceberg" or name.startswith("pyiceberg.")
    for name in sys.modules
)
"""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [os.path.join(project_root, "src"), os.environ.get("PYTHONPATH", "")]
        ),
    }
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"TRAX_IO_FEATURE_ONLINE_TABLE": ""},
    ],
)
def test_serving_runtime_requires_online_table(environ: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="TRAX_IO_FEATURE_ONLINE_TABLE is required"):
        build_native_online_runtime_from_env(
            "acme",
            environ=environ,
            dynamodb_resource=_DynamoResource(),
        )
