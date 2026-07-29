"""Static compatibility checks for the exact AWS Glue 4.0 runtime.

Developer tests intentionally may execute on a newer local Spark. These checks
keep the deployed source compatible with Glue's Python 3.10 / Spark 3.3 API
surface instead of allowing a newer workstation API to slip into production.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
GLUE_SOURCE = PROJECT_ROOT / "src" / "trax_io_feature_store" / "glue"
PACKAGE_SOURCE = PROJECT_ROOT / "src" / "trax_io_feature_store"
POPULATION_RUNTIME_SOURCES = (
    PACKAGE_SOURCE / "client.py",
    PACKAGE_SOURCE / "iceberg_store.py",
    PACKAGE_SOURCE / "materialize.py",
    PACKAGE_SOURCE / "online_runtime.py",
    PACKAGE_SOURCE / "online_store.py",
    PACKAGE_SOURCE / "online_writer.py",
    PACKAGE_SOURCE / "runtime.py",
    PACKAGE_SOURCE / "schemas" / "__init__.py",
    PACKAGE_SOURCE / "schemas" / "features.py",
)


def _runtime_contract() -> dict[str, str]:
    payload = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    return payload["tool"]["trax-io"]["glue-runtime"]


def _glue_sources() -> list[Path]:
    return sorted(GLUE_SOURCE.glob("*.py"))


def test_runtime_contract_is_pinned_to_glue_4() -> None:
    assert _runtime_contract() == {
        "aws-glue": "4.0",
        "python": "3.10",
        "pyspark": "3.3.0",
    }


def test_every_glue_module_parses_as_python_310() -> None:
    for path in _glue_sources():
        ast.parse(
            path.read_text(),
            filename=str(path),
            feature_version=(3, 10),
        )


def test_online_population_runtime_parses_as_python_310() -> None:
    for path in POPULATION_RUNTIME_SOURCES:
        ast.parse(
            path.read_text(),
            filename=str(path),
            feature_version=(3, 10),
        )


def test_glue_modules_avoid_newer_python_and_spark_symbols() -> None:
    errors: list[str] = []
    for path in _glue_sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "datetime"
                and any(alias.name == "UTC" for alias in node.names)
            ):
                errors.append(f"{path.name}:{node.lineno}: datetime.UTC requires Python 3.11")
            if isinstance(node, ast.Attribute) and node.attr == "try_to_timestamp":
                errors.append(
                    f"{path.name}:{node.lineno}: try_to_timestamp requires Spark 3.5"
                )
    assert errors == []


def test_all_glue_modules_import_without_pydantic_installed() -> None:
    modules = [
        f"trax_io_feature_store.glue.{path.stem}"
        for path in _glue_sources()
    ]
    script = f"""
import importlib
import importlib.abc
import sys

for name in list(sys.modules):
    if name == "pydantic" or name.startswith("pydantic."):
        del sys.modules[name]

class BlockPydantic(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pydantic" or fullname.startswith("pydantic."):
            raise ModuleNotFoundError("pydantic intentionally absent in Glue smoke test")
        return None

sys.meta_path.insert(0, BlockPydantic())
for module in {modules!r}:
    importlib.import_module(module)
assert not any(
    name == "pydantic" or name.startswith("pydantic.")
    for name in sys.modules
)
"""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [
                str(PROJECT_ROOT / "src"),
                os.environ.get("PYTHONPATH", ""),
            ]
        ),
    }
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
