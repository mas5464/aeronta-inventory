from datetime import date

import pytest
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
    # Jan=1, Feb=0 (gap-filled), Mar=3; non-zero periods are normalized
    # from their calendar-day rates to the common 30-day period duration.
    assert s.values == pytest.approx((30 / 31, 0.0, 90 / 31))
    assert s.bucket == "month" and s.days_per_period == 30.0


def test_zero_fills_missing_periods() -> None:
    obs = [
        DemandObservation(bucket="month", period_start=date(2026, 1, 1), removals=1),
        DemandObservation(bucket="month", period_start=date(2026, 4, 1), removals=2),  # gap Feb/Mar
    ]
    s = to_period_series(_history(obs))
    assert s.values == pytest.approx(
        (30 / 31, 0.0, 0.0, 2.0)
    )


def test_empty_history_is_empty_series() -> None:
    s = to_period_series(_history([]))
    assert s.values == () and s.days_per_period == 30.44


def test_configured_window_zero_fills_leading_interior_and_trailing_periods() -> None:
    history = DemandHistory(
        tenant_id="acme",
        pn="PN-A",
        location="LOC-1",
        observation_start=date(2026, 1, 1),
        observation_end=date(2026, 5, 31),
        bucket="month",
        observations=(
            DemandObservation(
                bucket="month",
                period_start=date(2026, 2, 1),
                removals=1,
            ),
            DemandObservation(
                bucket="month",
                period_start=date(2026, 4, 1),
                removals=2,
            ),
        ),
        extract_date=date(2026, 6, 1),
    )

    series = to_period_series(history)
    assert series.values == pytest.approx(
        (0.0, series.days_per_period / 28, 0.0, 2 * series.days_per_period / 30, 0.0)
    )


def test_mixed_buckets_fail_loudly() -> None:
    history = _history(
        [
            DemandObservation(bucket="month", period_start=date(2026, 1, 1), removals=1),
            DemandObservation(bucket="day", period_start=date(2026, 1, 2), removals=1),
        ]
    )

    with pytest.raises(ValueError, match="bucket"):
        to_period_series(history)


def test_days_per_period_reconciles_to_mid_bucket_inclusive_exposure() -> None:
    history = DemandHistory(
        tenant_id="acme",
        pn="PN-A",
        location="LOC-1",
        observation_start=date(2023, 4, 16),
        observation_end=date(2026, 4, 16),
        bucket="month",
        observations=(
            DemandObservation(
                bucket="month",
                period_start=date(2026, 4, 1),
                removals=1,
            ),
        ),
        extract_date=date(2026, 4, 16),
    )

    series = to_period_series(history)

    assert len(series.values) == 37
    assert series.days_per_period == pytest.approx(1097 / 37)
    assert series.days_per_period * len(series.values) == pytest.approx(1097)


def test_partial_boundary_months_are_normalized_to_the_same_daily_rate() -> None:
    history = DemandHistory(
        tenant_id="acme",
        pn="PN-A",
        location="LOC-1",
        observation_start=date(2026, 4, 16),
        observation_end=date(2026, 6, 15),
        bucket="month",
        observations=(
            DemandObservation(
                bucket="month",
                period_start=date(2026, 4, 1),
                issues=15,
            ),
            DemandObservation(
                bucket="month",
                period_start=date(2026, 5, 1),
                issues=31,
            ),
            DemandObservation(
                bucket="month",
                period_start=date(2026, 6, 1),
                issues=15,
            ),
        ),
        extract_date=date(2026, 6, 15),
    )

    series = to_period_series(history)

    assert series.days_per_period == pytest.approx(61 / 3)
    assert series.values == pytest.approx((61 / 3, 61 / 3, 61 / 3))
