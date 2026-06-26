"""Bind-variable resolution for the 21 extract domains.

Per the manifest contract, the four date-windowed domains take
Python ``datetime.date`` values; snapshot domains return empty dicts.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

from trax_io_extract.domains import Domain


def resolve_binds(
    domain: Domain,
    *,
    extract_date: date,
    window_days: int,
    demand_history_months: int,
    transaction: str | None,
) -> dict[str, Any]:
    """Return the bind dict for ``domain`` per the contract."""
    if domain.name == "causal_values":
        return {
            "start_date": extract_date - timedelta(days=window_days),
            "end_date": extract_date,
        }
    if domain.name in {"demand_history_rotables", "demand_history_expendables"}:
        return {
            "from_date": extract_date - relativedelta(months=demand_history_months),
            "to_date": extract_date,
        }
    if domain.name == "events":
        if transaction is None:
            raise ValueError(
                "`events` domain requires a non-None transaction bind value"
            )
        return {
            "as_of_date": extract_date,
            "transaction": transaction,
        }
    return {}
