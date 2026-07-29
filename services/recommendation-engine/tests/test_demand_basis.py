from __future__ import annotations

from datetime import date

import pytest
from trax_io_feature_store.schemas import DemandHistory, DemandObservation

from trax_io_reco.demand.basis import historical_demand_stats


def test_configured_36_month_window_uses_its_inclusive_exposure() -> None:
    history = DemandHistory(
        tenant_id="acme",
        pn="P",
        location="L",
        observation_start=date(2023, 4, 16),
        observation_end=date(2026, 4, 16),
        bucket="month",
        event_count_source="observed",
        observations=[
            DemandObservation(
                bucket="month",
                period_start=date(2026, 4, 1),
                issues=36,
                removal_events=0,
                issue_events=1,
            )
        ],
        extract_date=date(2026, 4, 16),
    )

    stats = historical_demand_stats(history)

    assert stats.trace.exposure_days == 1097
    assert stats.trace.observation_window_source == "configured"
    assert stats.trace.demanded_units == 36
    assert stats.trace.historical_per_day == pytest.approx(36 / 1097)
    assert stats.trace.historical_per_day != pytest.approx(36 / 730)


def test_leading_interior_and_trailing_periods_are_zero_filled() -> None:
    history = DemandHistory(
        tenant_id="acme",
        pn="P",
        location="L",
        observation_start=date(2026, 1, 1),
        observation_end=date(2026, 5, 31),
        bucket="month",
        event_count_source="observed",
        observations=[
            DemandObservation(
                bucket="month",
                period_start=date(2026, 2, 1),
                issues=28,
                removal_events=0,
                issue_events=1,
            ),
            DemandObservation(
                bucket="month",
                period_start=date(2026, 4, 1),
                issues=30,
                removal_events=0,
                issue_events=1,
            ),
        ],
        extract_date=date(2026, 6, 1),
    )

    stats = historical_demand_stats(history)

    assert stats.trace.observed_periods == 2
    assert stats.trace.zero_filled_periods == 3
    assert stats.daily_rates == pytest.approx((0.0, 1.0, 0.0, 1.0, 0.0))
    assert stats.variance_per_day > 0


def test_quantity_greater_than_one_is_still_one_observed_event() -> None:
    history = DemandHistory(
        tenant_id="acme",
        pn="P",
        location="L",
        observation_start=date(2026, 4, 1),
        observation_end=date(2026, 4, 30),
        bucket="month",
        event_count_source="observed",
        observations=[
            DemandObservation(
                bucket="month",
                period_start=date(2026, 4, 1),
                issues=7,
                removal_events=0,
                issue_events=1,
            )
        ],
        extract_date=date(2026, 5, 1),
    )

    trace = historical_demand_stats(history).trace

    assert trace.demanded_units == 7
    assert trace.demand_event_count == 1
    assert trace.event_count_source == "observed"


def test_legacy_counts_use_one_event_per_nonzero_bucket() -> None:
    history = DemandHistory(
        tenant_id="acme",
        pn="P",
        location="L",
        observations=[
            DemandObservation(
                bucket="month",
                period_start=date(2026, 1, 1),
                issues=9,
            ),
            DemandObservation(
                bucket="month",
                period_start=date(2026, 2, 1),
                issues=0,
            ),
            DemandObservation(
                bucket="month",
                period_start=date(2026, 3, 1),
                removals=3,
            ),
        ],
        extract_date=date(2026, 4, 1),
    )

    trace = historical_demand_stats(history).trace

    assert trace.demand_event_count == 2
    assert trace.event_count_source == "bucket_fallback"
    assert trace.demanded_units == 12
    assert trace.observation_window_source == "observed_span"


def test_empty_legacy_history_is_explicitly_unavailable() -> None:
    history = DemandHistory(
        tenant_id="acme",
        pn="P",
        location="L",
        observations=[],
        extract_date=date(2026, 4, 1),
    )

    trace = historical_demand_stats(history).trace

    assert trace.exposure_days == 0
    assert trace.observation_window_source == "unavailable"
    assert trace.demand_event_count is None
    assert trace.event_count_source == "unavailable"
    assert trace.historical_per_day == 0.0


def test_zero_marker_retains_window_but_counts_as_zero_filled() -> None:
    history = DemandHistory(
        tenant_id="acme",
        pn="P-ZERO",
        location="L",
        observation_start=date(2026, 1, 1),
        observation_end=date(2026, 3, 31),
        bucket="month",
        event_count_source="observed",
        observations=[
            DemandObservation(
                bucket="month",
                period_start=date(2026, 1, 1),
                removals=0,
                issues=0,
                removal_events=0,
                issue_events=0,
            )
        ],
        extract_date=date(2026, 4, 1),
    )

    trace = historical_demand_stats(history).trace

    assert trace.exposure_days == 90
    assert trace.demand_event_count == 0
    assert trace.demanded_units == 0
    assert trace.observed_periods == 0
    assert trace.zero_filled_periods == 3
