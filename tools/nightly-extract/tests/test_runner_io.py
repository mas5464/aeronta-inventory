"""Disk I/O invariants for per-domain JSON output."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest

from trax_io_extract.domains import DOMAINS_BY_NAME
from trax_io_extract.landing import LocalFsSink
from trax_io_extract.runner import run_extract


class FakeCursor:
    def __init__(self, rows: list[tuple], columns: list[str]):
        self._rows = rows
        self._columns = columns
        self.description = [(c, None, None, None, None, None, None) for c in columns]

    def execute(self, sql: str, binds: dict) -> None:
        pass

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, rows: list[tuple], columns: list[str]):
        self._rows = rows
        self._columns = columns

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._rows, self._columns)

    def close(self) -> None:
        pass


def make_factory(rows: list[tuple], columns: list[str]):
    @contextmanager
    def factory() -> Iterator[FakeConnection]:
        yield FakeConnection(rows, columns)

    return factory


@pytest.fixture
def sql_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "sql"


def test_domain_json_is_utf8_sorted_keys(tmp_path: Path, sql_dir: Path) -> None:
    rows = [("ATL", "Atlanta"), ("MIA", "Miami"), ("LAX", "Los Angeles")]
    factory = make_factory(rows, ["location_id", "name"])

    manifest = run_extract(
        domains_to_run=[DOMAINS_BY_NAME["location_master"]],
        sql_dir=sql_dir,
        sink=LocalFsSink(tmp_path),
        bind_resolver=lambda d: {},
        conn_factory=factory,
        tenant_id="t",
        extract_date=date(2026, 4, 16),
        run_id="01JIO001",
    )

    artifact = manifest.artifacts[0]
    path = tmp_path / "location_master.json"
    raw = path.read_bytes()

    # UTF-8 decodable.
    raw.decode("utf-8")

    # Valid JSON, correct row count.
    parsed = json.loads(raw)
    assert len(parsed) == 3
    assert artifact.row_count == 3

    # Sorted keys: within each row dict the keys are sorted lex.
    for obj in parsed:
        keys = list(obj.keys())
        assert keys == sorted(keys)

    # sha256 on the manifest matches what's on disk.
    assert artifact.sha256 == hashlib.sha256(raw).hexdigest()
    assert artifact.bytes == len(raw)


def test_lowercase_column_names(tmp_path: Path, sql_dir: Path) -> None:
    rows = [("A",)]
    factory = make_factory(rows, ["LOCATION_ID"])

    run_extract(
        domains_to_run=[DOMAINS_BY_NAME["location_master"]],
        sql_dir=sql_dir,
        sink=LocalFsSink(tmp_path),
        bind_resolver=lambda d: {},
        conn_factory=factory,
        tenant_id="t",
        extract_date=date(2026, 4, 16),
        run_id="01JIO002",
    )

    parsed = json.loads((tmp_path / "location_master.json").read_text("utf-8"))
    assert parsed == [{"location_id": "A"}]
