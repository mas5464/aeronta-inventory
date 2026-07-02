"""Tests for the station + part-cap scope filter (offline, no live DB)."""

from __future__ import annotations

import pytest

from trax_io_extract.scope import (
    ExtractScope,
    resolve_scope,
    resolve_scope_planning_active,
    wrap_scoped_sql,
)

INNER_SQL = "SELECT PN AS hostpartid, LOCATION AS hostlocid FROM STOCK_AMOUNT ;"


def test_wrap_part_location_scope() -> None:
    scope = ExtractScope(location="YYZ", parts=("PN1", "PN2"))
    sql, binds = wrap_scoped_sql(INNER_SQL, "part_location", scope)

    assert "SELECT * FROM (" in sql
    assert "traxscope.hostpartid IN (" in sql
    assert "traxscope.hostlocid = :scope_loc" in sql
    # Inner trailing ';' must be stripped, not left dangling mid-statement.
    assert ";" not in sql

    assert binds == {"scope_p0": "PN1", "scope_p1": "PN2", "scope_loc": "YYZ"}


def test_wrap_part_scope_omits_location_clause() -> None:
    scope = ExtractScope(location="YYZ", parts=("PN1", "PN2", "PN3"))
    sql, binds = wrap_scoped_sql(INNER_SQL, "part", scope)

    assert "SELECT * FROM (" in sql
    assert "traxscope.hostpartid IN (" in sql
    assert "traxscope.hostlocid" not in sql
    assert "scope_loc" not in binds

    assert binds == {"scope_p0": "PN1", "scope_p1": "PN2", "scope_p2": "PN3"}


def test_none_scope_key_returns_unchanged() -> None:
    scope = ExtractScope(location="YYZ", parts=("PN1",))
    sql, binds = wrap_scoped_sql(INNER_SQL, None, scope)
    assert sql == INNER_SQL
    assert binds == {}


def test_none_scope_returns_unchanged() -> None:
    sql, binds = wrap_scoped_sql(INNER_SQL, "part_location", None)
    assert sql == INNER_SQL
    assert binds == {}


def test_both_none_returns_unchanged() -> None:
    sql, binds = wrap_scoped_sql(INNER_SQL, None, None)
    assert sql == INNER_SQL
    assert binds == {}


def test_over_1000_parts_chunks_into_ored_in_lists() -> None:
    scope = ExtractScope(location="YYZ", parts=tuple(f"PN{i}" for i in range(2500)))
    sql, binds = wrap_scoped_sql(INNER_SQL, "part_location", scope)

    assert sql.count("traxscope.hostpartid IN (") == 3
    assert sql.count(" OR ") == 2
    assert "traxscope.hostlocid = :scope_loc" in sql
    assert ";" not in sql

    assert len(binds) == 2501  # 2500 part binds + scope_loc
    assert binds["scope_p0"] == "PN0"
    assert binds["scope_p2499"] == "PN2499"
    assert binds["scope_loc"] == "YYZ"


def test_exactly_1000_parts_is_one_chunk() -> None:
    scope = ExtractScope(location="YYZ", parts=tuple(f"PN{i}" for i in range(1000)))
    sql, binds = wrap_scoped_sql(INNER_SQL, "part_location", scope)
    assert sql.count("traxscope.hostpartid IN (") == 1
    assert " OR " not in sql
    assert len(binds) == 1001  # 1000 part binds + scope_loc


def test_1001_parts_is_two_chunks() -> None:
    scope = ExtractScope(location="YYZ", parts=tuple(f"PN{i}" for i in range(1001)))
    sql, binds = wrap_scoped_sql(INNER_SQL, "part_location", scope)
    assert sql.count("traxscope.hostpartid IN (") == 2
    assert sql.count(" OR ") == 1
    assert len(binds) == 1002  # 1001 part binds + scope_loc


def test_network_wide_part_location_scope_omits_location_clause() -> None:
    scope = ExtractScope(location=None, parts=("PN1", "PN2"))
    sql, binds = wrap_scoped_sql(INNER_SQL, "part_location", scope)

    assert "traxscope.hostpartid IN (" in sql
    assert ":scope_loc" not in sql
    assert "scope_loc" not in binds
    assert binds == {"scope_p0": "PN1", "scope_p1": "PN2"}


def test_network_wide_part_location_scope_filters_planning_active() -> None:
    # W3-5 (spec): on the network-wide path the part_location domains (policy #19,
    # part_location #4) must land ONLY planning-active rows. Without this predicate a
    # planning-active PART contributes every one of its location rows and the key
    # universe explodes (984,021 keys observed vs the true 62,492 on the real DB).
    scope = ExtractScope(location=None, parts=("PN1",))
    sql, binds = wrap_scoped_sql(INNER_SQL, "part_location", scope)

    assert "EXISTS (SELECT 1 FROM PN_INVENTORY_LEVEL pil" in sql
    assert "pil.PN = traxscope.hostpartid" in sql
    assert "pil.LOCATION = traxscope.hostlocid" in sql
    assert "NVL(pil.REORDER_LEVEL,0)>0 OR NVL(pil.MAXIMUM_STOCK,0)>0" in sql
    assert binds == {"scope_p0": "PN1"}


def test_station_part_location_scope_has_no_planning_predicate() -> None:
    # The single-station path (Wave 1) is unchanged: hostlocid bind, no EXISTS.
    scope = ExtractScope(location="YYZ", parts=("PN1",))
    sql, _ = wrap_scoped_sql(INNER_SQL, "part_location", scope)
    assert "EXISTS" not in sql
    assert "traxscope.hostlocid = :scope_loc" in sql


def test_network_wide_part_scope_has_no_planning_predicate() -> None:
    # scope_key="part" domains (demand, vendor, master data) are keyed per-PN, not
    # per-(PN, location) — the planning-active row filter must not apply to them.
    scope = ExtractScope(location=None, parts=("PN1",))
    sql, _ = wrap_scoped_sql(INNER_SQL, "part", scope)
    assert "EXISTS" not in sql


def test_unknown_scope_key_raises() -> None:
    scope = ExtractScope(location="YYZ", parts=("PN1",))
    with pytest.raises(ValueError, match="unknown scope_key"):
        wrap_scoped_sql(INNER_SQL, "bogus", scope)


def test_wrap_strips_trailing_semicolon_and_whitespace() -> None:
    scope = ExtractScope(location="YYZ", parts=("PN1",))
    sql, _ = wrap_scoped_sql("SELECT 1 FROM DUAL ;\n", "part", scope)
    assert sql == "SELECT * FROM ( SELECT 1 FROM DUAL ) traxscope WHERE traxscope.hostpartid IN (:scope_p0)"


# ---------------------------------------------------------------------------
# resolve_scope — fake-connection tests (no live DB)


class _FakeCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.executed_sql: str | None = None
        self.last_binds: dict | None = None

    def execute(self, sql: str, binds: dict) -> None:
        self.executed_sql = sql
        self.last_binds = dict(binds)

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.cursors: list[_FakeCursor] = []

    def cursor(self) -> _FakeCursor:
        cur = _FakeCursor(self._rows)
        self.cursors.append(cur)
        return cur


def test_resolve_scope_returns_location_and_parts() -> None:
    conn = _FakeConnection(rows=[("PN1",), ("PN2",), ("PN3",)])
    scope = resolve_scope(conn, location="YYZ", max_parts=5)
    assert scope == ExtractScope(location="YYZ", parts=("PN1", "PN2", "PN3"))


def test_resolve_scope_binds_location_and_cap() -> None:
    conn = _FakeConnection(rows=[])
    resolve_scope(conn, location="YYZ", max_parts=5)
    cur = conn.cursors[0]
    assert cur.last_binds == {"loc": "YYZ", "cap": 5}
    assert "PN_INVENTORY_LEVEL" in cur.executed_sql
    assert "REORDER_LEVEL" in cur.executed_sql
    assert "MAXIMUM_STOCK" in cur.executed_sql
    assert "FETCH FIRST :cap ROWS ONLY" in cur.executed_sql


# ---------------------------------------------------------------------------
# resolve_scope_planning_active — network-wide scope (Wave 3)


def test_resolve_scope_planning_active_returns_none_location_and_parts() -> None:
    conn = _FakeConnection(rows=[("PN1",), ("PN2",), ("PN3",)])
    scope = resolve_scope_planning_active(conn, max_parts=100000)
    assert scope == ExtractScope(location=None, parts=("PN1", "PN2", "PN3"))


def test_resolve_scope_planning_active_binds_cap_only_no_location_predicate() -> None:
    conn = _FakeConnection(rows=[])
    resolve_scope_planning_active(conn, max_parts=100000)
    cur = conn.cursors[0]
    assert cur.last_binds == {"cap": 100000}
    assert "PN_INVENTORY_LEVEL" in cur.executed_sql
    assert "REORDER_LEVEL" in cur.executed_sql
    assert "MAXIMUM_STOCK" in cur.executed_sql
    assert "LOCATION" not in cur.executed_sql
    assert "FETCH FIRST :cap ROWS ONLY" in cur.executed_sql
