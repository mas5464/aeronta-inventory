from datetime import date

from trax_io_feature_store.schemas import DemandHistory, DemandObservation

from trax_io_forecasting.series import to_period_series


def _history(obs: list[DemandObservation]) -> DemandHistory:
    return DemandHistory(
        tenant_id="acme",
        pn="PN-A",
        location="LOC-1",
        observations=tuple(obs),
        extract_date=date(2026, 1, 1),
    )


def test_orders_and_sums_removals_plus_issues() -> None:
    obs = [
        DemandObservation(bucket="month", period_start=date(2026, 3, 1), removals=2, issues=1),
        DemandObservation(bucket="month", period_start=date(2026, 1, 1), removals=1, issues=0),
    ]
    s = to_period_series(_history(obs))
    # Jan=1, Feb=0 (gap-filled), Mar=2+1=3 — sorted by period_start, dense
    assert s.values == (1.0, 0.0, 3.0)
    assert s.bucket == "month" and s.days_per_period == 30.44


def test_zero_fills_missing_periods() -> None:
    obs = [
        DemandObservation(bucket="month", period_start=date(2026, 1, 1), removals=1),
        DemandObservation(bucket="month", period_start=date(2026, 4, 1), removals=2),  # gap Feb/Mar
    ]
    s = to_period_series(_history(obs))
    assert s.values == (1.0, 0.0, 0.0, 2.0)  # Jan, Feb=0, Mar=0, Apr


def test_empty_history_is_empty_series() -> None:
    s = to_period_series(_history([]))
    assert s.values == () and s.days_per_period == 30.44
