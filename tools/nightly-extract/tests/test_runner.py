"""Runner tests with a fake Oracle connection factory."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest

from trax_io_extract.domains import DOMAINS, DOMAINS_BY_NAME
from trax_io_extract.landing import LocalFsSink
from trax_io_extract.oracle import OracleExecutionError
from trax_io_extract.runner import run_extract


# ---------------------------------------------------------------------------
# Fakes


class FakeCursor:
    def __init__(self, *, rows: list[tuple], columns: list[str], raise_error: OracleExecutionError | None):
        self._rows = rows
        self._columns = columns
        self._raise = raise_error
        self.last_binds: dict | None = None
        self.description = [(c, None, None, None, None, None, None) for c in columns]

    def execute(self, sql: str, binds: dict) -> None:  # noqa: ARG002
        self.last_binds = dict(binds)
        if self._raise is not None:
            # Simulate the wrapped error path via execute_domain — we raise
            # OracleExecutionError directly from the fake cursor via a
            # sentinel side-channel: the runner catches OracleExecutionError
            # from execute_domain, which in turn catches oracledb.DatabaseError.
            # For test simplicity we raise it at the cursor level via a
            # monkeypatched execute_domain in the per-domain fail tests.
            raise self._raise

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, *, rows: list[tuple], columns: list[str]):
        self._rows = rows
        self._columns = columns
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        cur = FakeCursor(rows=self._rows, columns=self._columns, raise_error=None)
        self.cursors.append(cur)
        return cur

    def close(self) -> None:
        pass


def make_factory(rows: list[tuple], columns: list[str]):
    @contextmanager
    def factory() -> Iterator[FakeConnection]:
        conn = FakeConnection(rows=rows, columns=columns)
        try:
            yield conn
        finally:
            conn.close()

    return factory


def _bind_resolver_empty(domain):  # noqa: ANN001
    return {}


@pytest.fixture
def sql_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "sql"


# ---------------------------------------------------------------------------
# Tests


def test_happy_path_two_domains_all_succeed(tmp_path: Path, sql_dir: Path) -> None:
    to_run = [DOMAINS_BY_NAME["location_master"], DOMAINS_BY_NAME["vendor"]]
    factory = make_factory([("LOC1", "Miami"), ("LOC2", "Atlanta")], ["location_id", "name"])

    manifest = run_extract(
        domains_to_run=to_run,
        sql_dir=sql_dir,
        sink=LocalFsSink(tmp_path),
        bind_resolver=_bind_resolver_empty,
        conn_factory=factory,
        tenant_id="t1",
        extract_date=date(2026, 4, 16),
        run_id="01JRUN0001",
    )

    assert manifest.run_status == "succeeded"
    assert len(manifest.artifacts) == 2
    assert all(a.status == "succeeded" for a in manifest.artifacts)
    assert all(a.row_count == 2 for a in manifest.artifacts)
    assert (tmp_path / "manifest.json").is_file()
    assert (tmp_path / "location_master.json").is_file()
    assert (tmp_path / "vendor.json").is_file()


def test_one_domain_fails_partial(tmp_path: Path, sql_dir: Path, monkeypatch) -> None:
    """One domain raises OracleExecutionError; others succeed → partial."""
    failing_domain = "vendor"

    import trax_io_extract.runner as runner_mod

    real_execute = runner_mod.execute_domain

    def fake_execute(*, conn, sql_text, binds):
        # Identify the domain by inspecting the SQL filename in the sql_text.
        if "vendor" in sql_text.lower() and "21_vendor" not in sql_text.lower():
            # Both SQL files contain 'vendor' so use a sentinel: raise if cursor's
            # description marker matches. Simpler: use a thread-local toggle.
            pass
        return real_execute(conn=conn, sql_text=sql_text, binds=binds)

    # Simpler approach: patch execute_domain to raise only when called for vendor.
    # We use sql filename lookup: read the sql path via binds? We don't have domain
    # here, so we toggle by call count.
    call_count = {"n": 0}

    def toggling(*, conn, sql_text, binds):  # noqa: ANN001
        call_count["n"] += 1
        if call_count["n"] == 2:  # second domain = vendor
            raise OracleExecutionError("ORA-00942", "table or view does not exist")
        return real_execute(conn=conn, sql_text=sql_text, binds=binds)

    monkeypatch.setattr(runner_mod, "execute_domain", toggling)

    to_run = [DOMAINS_BY_NAME["location_master"], DOMAINS_BY_NAME[failing_domain]]
    factory = make_factory([("LOC1", "Miami")], ["location_id", "name"])

    manifest = run_extract(
        domains_to_run=to_run,
        sql_dir=sql_dir,
        sink=LocalFsSink(tmp_path),
        bind_resolver=_bind_resolver_empty,
        conn_factory=factory,
        tenant_id="t1",
        extract_date=date(2026, 4, 16),
        run_id="01JRUN0002",
    )

    assert manifest.run_status == "partial"
    by_name = {a.domain: a for a in manifest.artifacts}
    assert by_name["location_master"].status == "succeeded"
    assert by_name[failing_domain].status == "failed"
    assert by_name[failing_domain].error_code == "ORA-00942"
    assert by_name[failing_domain].error_message == "table or view does not exist"
    # File should NOT be written for failed domains.
    assert not (tmp_path / "vendor.json").is_file()


def test_all_windowed_fail_but_snapshot_succeeds_is_degraded(
    tmp_path: Path, sql_dir: Path, monkeypatch
) -> None:
    import trax_io_extract.runner as runner_mod

    real_execute = runner_mod.execute_domain

    windowed_names = {
        "causal_values",
        "demand_history_rotables",
        "demand_history_expendables",
        "events",
    }

    def selective(*, conn, sql_text, binds):  # noqa: ANN001
        # Look at the SQL text to decide. Each domain's SQL file is distinct —
        # we pull the filename by checking for a marker substring. But we don't
        # have that easily; easier: inspect binds keys for a windowed domain.
        keys = set(binds.keys())
        if keys & {"start_date", "from_date", "as_of_date"}:
            raise OracleExecutionError("ORA-00001", "windowed failure")
        return real_execute(conn=conn, sql_text=sql_text, binds=binds)

    monkeypatch.setattr(runner_mod, "execute_domain", selective)

    def resolver(domain):  # noqa: ANN001
        name = domain.name
        if name == "causal_values":
            return {"start_date": date(2026, 1, 1), "end_date": date(2026, 4, 16)}
        if name in {"demand_history_rotables", "demand_history_expendables"}:
            return {"from_date": date(2023, 4, 16), "to_date": date(2026, 4, 16)}
        if name == "events":
            return {"as_of_date": date(2026, 4, 16), "transaction": "NR"}
        return {}

    to_run = [
        DOMAINS_BY_NAME["causal_values"],
        DOMAINS_BY_NAME["demand_history_rotables"],
        DOMAINS_BY_NAME["demand_history_expendables"],
        DOMAINS_BY_NAME["events"],
        DOMAINS_BY_NAME["location_master"],
    ]
    factory = make_factory([("LOC1", "Miami")], ["location_id", "name"])

    manifest = run_extract(
        domains_to_run=to_run,
        sql_dir=sql_dir,
        sink=LocalFsSink(tmp_path),
        bind_resolver=resolver,
        conn_factory=factory,
        tenant_id="t1",
        extract_date=date(2026, 4, 16),
        run_id="01JRUN0003",
    )

    assert manifest.run_status == "degraded"
    by_name = {a.domain: a.status for a in manifest.artifacts}
    for name in windowed_names:
        assert by_name[name] == "failed"
    assert by_name["location_master"] == "succeeded"


def test_every_domain_fails_is_failed(tmp_path: Path, sql_dir: Path, monkeypatch) -> None:
    import trax_io_extract.runner as runner_mod

    def always_fail(*, conn, sql_text, binds):  # noqa: ANN001, ARG001
        raise OracleExecutionError("ORA-12154", "could not resolve connect identifier")

    monkeypatch.setattr(runner_mod, "execute_domain", always_fail)

    to_run = [DOMAINS_BY_NAME["location_master"], DOMAINS_BY_NAME["vendor"]]
    factory = make_factory([], ["x"])

    manifest = run_extract(
        domains_to_run=to_run,
        sql_dir=sql_dir,
        sink=LocalFsSink(tmp_path),
        bind_resolver=_bind_resolver_empty,
        conn_factory=factory,
        tenant_id="t1",
        extract_date=date(2026, 4, 16),
        run_id="01JRUN0004",
    )

    assert manifest.run_status == "failed"
    assert all(a.status == "failed" for a in manifest.artifacts)
    assert all(a.error_code == "ORA-12154" for a in manifest.artifacts)


def test_per_domain_isolation_does_not_abort_remaining(
    tmp_path: Path, sql_dir: Path, monkeypatch
) -> None:
    """Middle domain fails; later domains still run."""
    import trax_io_extract.runner as runner_mod

    real_execute = runner_mod.execute_domain
    call_count = {"n": 0}

    def middle_fail(*, conn, sql_text, binds):  # noqa: ANN001
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OracleExecutionError("ORA-00942", "bad table")
        return real_execute(conn=conn, sql_text=sql_text, binds=binds)

    monkeypatch.setattr(runner_mod, "execute_domain", middle_fail)

    to_run = [
        DOMAINS_BY_NAME["location_master"],
        DOMAINS_BY_NAME["vendor"],
        DOMAINS_BY_NAME["trans_code"],
    ]
    factory = make_factory([("LOC1",)], ["location_id"])

    manifest = run_extract(
        domains_to_run=to_run,
        sql_dir=sql_dir,
        sink=LocalFsSink(tmp_path),
        bind_resolver=_bind_resolver_empty,
        conn_factory=factory,
        tenant_id="t1",
        extract_date=date(2026, 4, 16),
        run_id="01JRUN0005",
    )

    assert len(manifest.artifacts) == 3
    statuses = [a.status for a in manifest.artifacts]
    assert statuses == ["succeeded", "failed", "succeeded"]


def test_source_sql_sha256_matches_canonical_hash(tmp_path: Path, sql_dir: Path) -> None:
    """Manifest's source_sql_sha256 equals the canonical 21-file hash."""
    from trax_io_extract.runner import _compute_source_sql_sha256

    to_run = [DOMAINS_BY_NAME["location_master"]]
    factory = make_factory([], ["x"])
    manifest = run_extract(
        domains_to_run=to_run,
        sql_dir=sql_dir,
        sink=LocalFsSink(tmp_path),
        bind_resolver=_bind_resolver_empty,
        conn_factory=factory,
        tenant_id="t1",
        extract_date=date(2026, 4, 16),
        run_id="01JRUN0006",
    )
    assert manifest.source_sql_sha256 == _compute_source_sql_sha256(sql_dir)


# Ensure we cover all 21 domains in the registry.
def test_registry_has_21_domains() -> None:
    assert len(DOMAINS) == 21


def test_sink_failure_aborts_run_and_lands_no_manifest(tmp_path: Path, sql_dir: Path) -> None:
    """A sink write failure mid-run must propagate and NEVER land a manifest, so #2 Glue
    ignores the incomplete prefix (the change's load-bearing atomicity property)."""

    class RaisingSink:
        def __init__(self) -> None:
            self.written: list[str] = []

        def write(self, relative_path: str, payload: bytes) -> str:
            if relative_path == "location_master.json":
                raise RuntimeError("S3 unavailable")
            self.written.append(relative_path)
            return f"mem://{relative_path}"

    sink = RaisingSink()
    factory = make_factory([("LOC1", "Miami")], ["location_id", "name"])

    with pytest.raises(RuntimeError):
        run_extract(
            domains_to_run=[DOMAINS_BY_NAME["location_master"]],
            sql_dir=sql_dir,
            sink=sink,
            bind_resolver=_bind_resolver_empty,
            conn_factory=factory,
            tenant_id="t",
            extract_date=date(2026, 4, 16),
            run_id="01JFAIL",
        )
    assert "manifest.json" not in sink.written  # crashed run leaves no manifest
