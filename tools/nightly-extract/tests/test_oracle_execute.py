"""Tests for the ``execute_domain`` trailing-semicolon strip seam.

``oracledb``'s ``cursor.execute()`` rejects a semicolon-terminated statement
with ``ORA-00933``. All 21 extract SQL files end with a trailing ``;``, so
``execute_domain`` must strip exactly one trailing terminator (plus
surrounding whitespace) before calling ``cursor.execute()`` — without
touching any internal ``;`` in the statement.
"""

from __future__ import annotations

from trax_io_extract.oracle import execute_domain


class FakeCursor:
    """Records the exact SQL text passed to ``execute()``."""

    def __init__(self, *, rows: list[tuple], columns: list[str]) -> None:
        self._rows = rows
        self._columns = columns
        self.executed_sql: str | None = None
        self.last_binds: dict | None = None
        self.description = [(c, None, None, None, None, None, None) for c in columns]

    def execute(self, sql: str, binds: dict) -> None:
        self.executed_sql = sql
        self.last_binds = dict(binds)

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, *, rows: list[tuple], columns: list[str]) -> None:
        self._rows = rows
        self._columns = columns
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        cur = FakeCursor(rows=self._rows, columns=self._columns)
        self.cursors.append(cur)
        return cur

    def close(self) -> None:
        pass


def test_trailing_semicolon_with_space_is_stripped() -> None:
    conn = FakeConnection(rows=[], columns=["x"])
    execute_domain(conn=conn, sql_text="SELECT 1 FROM DUAL ;", binds={})
    assert conn.cursors[0].executed_sql == "SELECT 1 FROM DUAL"


def test_trailing_semicolon_with_newline_is_stripped() -> None:
    conn = FakeConnection(rows=[], columns=["x"])
    execute_domain(conn=conn, sql_text="SELECT 1 FROM DUAL;\n", binds={})
    assert conn.cursors[0].executed_sql == "SELECT 1 FROM DUAL"


def test_no_trailing_semicolon_passes_through_unchanged() -> None:
    conn = FakeConnection(rows=[], columns=["x"])
    execute_domain(conn=conn, sql_text="SELECT 1 FROM DUAL", binds={})
    assert conn.cursors[0].executed_sql == "SELECT 1 FROM DUAL"


def test_internal_semicolons_are_not_touched() -> None:
    """Only the single trailing terminator is stripped; internal ``;`` in a
    multi-line, plain-SQL statement (e.g. a CASE/subquery text literal) must
    survive untouched."""
    sql = "SELECT 'a;b' AS lit, x FROM t WHERE y = 1 ;"
    conn = FakeConnection(rows=[], columns=["x"])
    execute_domain(conn=conn, sql_text=sql, binds={})
    assert conn.cursors[0].executed_sql == "SELECT 'a;b' AS lit, x FROM t WHERE y = 1"


def test_returns_column_normalized_rows_and_row_count() -> None:
    conn = FakeConnection(
        rows=[("LOC1", "Miami"), ("LOC2", "Atlanta")],
        columns=[" HostLocID ", "NAME"],
    )
    rows, row_count = execute_domain(
        conn=conn, sql_text="SELECT HostLocID, NAME FROM LOCATION_MASTER ;", binds={}
    )
    assert row_count == 2
    assert rows == [
        {"hostlocid": "LOC1", "name": "Miami"},
        {"hostlocid": "LOC2", "name": "Atlanta"},
    ]
    assert conn.cursors[0].executed_sql == "SELECT HostLocID, NAME FROM LOCATION_MASTER"


def test_binds_are_forwarded_as_dict() -> None:
    conn = FakeConnection(rows=[], columns=["x"])
    execute_domain(
        conn=conn,
        sql_text="SELECT 1 FROM DUAL WHERE x = :as_of_date ;",
        binds={"as_of_date": "2026-04-16"},
    )
    assert conn.cursors[0].last_binds == {"as_of_date": "2026-04-16"}
