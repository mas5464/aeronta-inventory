"""Tests for bind-variable resolution across the 21 domains."""

from __future__ import annotations

from datetime import date

import pytest
from dateutil.relativedelta import relativedelta

from trax_io_extract.binds import resolve_binds
from trax_io_extract.domains import DOMAINS, DOMAINS_BY_NAME

EXTRACT = date(2026, 4, 16)


def _r(domain_name: str, *, transaction: str | None = "NR") -> dict:
    return resolve_binds(
        DOMAINS_BY_NAME[domain_name],
        extract_date=EXTRACT,
        window_days=90,
        demand_history_months=36,
        transaction=transaction,
    )


def test_causal_values_window() -> None:
    binds = _r("causal_values")
    assert binds == {
        "start_date": date(2026, 1, 16),
        "end_date": EXTRACT,
    }


def test_causal_values_respects_window_days() -> None:
    binds = resolve_binds(
        DOMAINS_BY_NAME["causal_values"],
        extract_date=EXTRACT,
        window_days=7,
        demand_history_months=36,
        transaction=None,
    )
    assert binds["start_date"] == date(2026, 4, 9)
    assert binds["end_date"] == EXTRACT


def test_demand_history_rotables_months() -> None:
    binds = _r("demand_history_rotables")
    assert binds == {
        "from_date": EXTRACT - relativedelta(months=36),
        "to_date": EXTRACT,
    }


def test_demand_history_expendables_months() -> None:
    binds = _r("demand_history_expendables")
    assert binds == {
        "from_date": EXTRACT - relativedelta(months=36),
        "to_date": EXTRACT,
    }


def test_demand_history_custom_months() -> None:
    binds = resolve_binds(
        DOMAINS_BY_NAME["demand_history_rotables"],
        extract_date=EXTRACT,
        window_days=90,
        demand_history_months=12,
        transaction=None,
    )
    assert binds["from_date"] == date(2025, 4, 16)


def test_events_requires_transaction() -> None:
    with pytest.raises(ValueError):
        resolve_binds(
            DOMAINS_BY_NAME["events"],
            extract_date=EXTRACT,
            window_days=90,
            demand_history_months=36,
            transaction=None,
        )


def test_events_happy_path() -> None:
    binds = _r("events", transaction="NR")
    assert binds == {"as_of_date": EXTRACT, "transaction": "NR"}


def test_all_snapshot_domains_are_empty() -> None:
    snapshot_domains = [d for d in DOMAINS if not d.date_windowed]
    assert len(snapshot_domains) == 17
    for d in snapshot_domains:
        assert resolve_binds(
            d,
            extract_date=EXTRACT,
            window_days=90,
            demand_history_months=36,
            transaction=None,
        ) == {}


def test_all_21_domains_resolvable() -> None:
    for d in DOMAINS:
        binds = resolve_binds(
            d,
            extract_date=EXTRACT,
            window_days=90,
            demand_history_months=36,
            transaction="NR",
        )
        assert isinstance(binds, dict)
        # Domain-declared bind vars must match the resolved keys.
        assert set(binds.keys()) == set(d.bind_vars)
