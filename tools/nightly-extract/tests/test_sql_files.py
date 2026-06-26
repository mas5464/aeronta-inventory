"""Assert the 21 SQL files exist, are non-empty, and carry the expected
bind variables for windowed domains.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trax_io_extract.domains import DOMAINS

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


@pytest.mark.parametrize("domain", DOMAINS, ids=[d.name for d in DOMAINS])
def test_sql_file_exists_and_non_empty(domain) -> None:  # type: ignore[no-untyped-def]
    path = SQL_DIR / domain.sql_file
    assert path.is_file(), f"missing SQL file {path}"
    content = path.read_text()
    assert content.strip(), f"empty SQL file {path}"
    # Header comment convention.
    assert content.startswith("-- Domain:"), f"missing header in {path}"
    assert f"Domain: {domain.name}" in content.splitlines()[0]


@pytest.mark.parametrize(
    "domain",
    [d for d in DOMAINS if d.date_windowed],
    ids=[d.name for d in DOMAINS if d.date_windowed],
)
def test_windowed_sql_has_bind_vars(domain) -> None:  # type: ignore[no-untyped-def]
    path = SQL_DIR / domain.sql_file
    content = path.read_text()
    for bind in domain.bind_vars:
        assert f":{bind}" in content, f"bind :{bind} missing from {path}"


def test_no_legacy_string_placeholders() -> None:
    """The legacy string-literal placeholders must not appear anywhere."""
    legacy = [
        "' startDate '",
        "' endDate   '",
        "'endDate'",
        "' fromDate '",
        "'  toDate '",
        "' date '",
        "' transaction '",
    ]
    for path in SQL_DIR.glob("*.sql"):
        content = path.read_text()
        for placeholder in legacy:
            assert placeholder not in content, (
                f"legacy placeholder {placeholder!r} still in {path.name}"
            )
