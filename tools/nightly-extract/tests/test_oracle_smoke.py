"""Env-gated smoke test against a real Oracle instance.

Skipped unless all four ``TRAX_ORACLE_*`` env vars are set. Read-only:
runs the ``05_location_master.sql`` extract query and asserts real rows
come back, proving the trailing-``;`` strip in ``execute_domain`` actually
works end-to-end against a live driver (not just the fakes).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trax_io_extract.oracle import (
    OracleConnectionConfig,
    execute_domain,
    oracle_connection,
)

REQUIRED_ENV = (
    "TRAX_ORACLE_HOST",
    "TRAX_ORACLE_SERVICE",
    "TRAX_ORACLE_USER",
    "TRAX_ORACLE_PASSWORD",
)


@pytest.mark.skipif(
    not all(os.getenv(v) for v in REQUIRED_ENV),
    reason="no live Oracle env",
)
def test_location_master_extract_returns_rows_from_live_oracle() -> None:
    sql_path = Path(__file__).resolve().parent.parent / "sql" / "05_location_master.sql"
    sql_text = sql_path.read_text()

    cfg = OracleConnectionConfig.from_env()

    with oracle_connection(cfg) as conn:
        rows, row_count = execute_domain(conn=conn, sql_text=sql_text, binds={})

    assert row_count > 0
    assert len(rows) == row_count
    for row in rows:
        assert "hostlocid" in row
