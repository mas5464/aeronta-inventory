"""Pure aggregation for matched, no-lookahead advisory replay observations."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from trax_io_reco.contracts.replay import (
    COMPARISON_RULE_DEFINITIONS,
    MatchedReplayObservation,
    ReplayCohortResult,
    ReplayEvaluationRequest,
    ReplayExclusionCount,
    ReplayMetricDefinition,
    ReplayMetrics,
    ReplayObservationLineageRef,
    ShadowScorecard,
    combined_outcome_manifest_sha256,
    expected_metric_delta,
)

_ZERO = Decimal("0")
_METRIC_DEFINITIONS = (
    ReplayMetricDefinition(
        metric="demanded_units",
        unit="units",
        denominator="realized demand in completed matched horizons",
        exclusions="cancelled, out-of-window, or unavailable demand",
    ),
    ReplayMetricDefinition(
        metric="filled_units",
        unit="units",
        denominator="realized demanded units in completed matched horizons",
        exclusions="fills outside the matched evaluation window",
    ),
    ReplayMetricDefinition(
        metric="fill_rate",
        unit="ratio",
        denominator="realized demanded units in completed matched horizons",
        exclusions="historical decisions excluded by the universe manifest",
    ),
    ReplayMetricDefinition(
        metric="backordered_units",
        unit="units",
        denominator="realized demanded units in completed matched horizons",
        exclusions="cancelled or out-of-window demand",
    ),
    ReplayMetricDefinition(
        metric="shortage_unit_days",
        unit="unit-days",
        denominator="daily unmet units across completed matched horizons",
        exclusions="days outside each evaluation horizon",
    ),
    ReplayMetricDefinition(
        metric="ending_inventory_units",
        unit="units",
        denominator="serviceable inventory at each evaluation horizon end",
        exclusions="unserviceable, rental, loan, and reserved stock",
    ),
    ReplayMetricDefinition(
        metric="inventory_investment",
        unit="tenant base currency",
        denominator="serviceable ending inventory valued at as-of prices",
        exclusions="decisions without historically available prices",
    ),
    ReplayMetricDefinition(
        metric="holding_cost",
        unit="tenant base currency",
        denominator="inventory exposure over each completed horizon",
        exclusions="days outside each matched evaluation horizon",
    ),
    ReplayMetricDefinition(
        metric="ordering_cost",
        unit="tenant base currency",
        denominator="orders proposed by the evaluated advisory policy",
        exclusions="cancelled proposals and every writeback action",
    ),
    ReplayMetricDefinition(
        metric="acquisition_cash",
        unit="tenant base currency",
        denominator="incremental acquisition committed in the replay horizon",
        exclusions="non-acquisition lifecycle costs",
    ),
    ReplayMetricDefinition(
        metric="aog_risk_proxy_events",
        unit="proxy events",
        denominator="approved AOG-risk proxy evaluated on matched decisions",
        exclusions="decisions where the approved proxy is unavailable",
    ),
    ReplayMetricDefinition(
        metric="decision_count",
        unit="decisions",
        denominator="evaluated historical decisions in the universe manifest",
        exclusions="decisions listed in the exclusion ledger",
    ),
)


def _aggregate(
    observations: tuple[MatchedReplayObservation, ...],
    *,
    side: str,
    currency: str,
) -> ReplayMetrics:
    metrics = [getattr(observation, side) for observation in observations]
    demanded = sum((metric.demanded_units for metric in metrics), _ZERO)
    filled = sum((metric.filled_units for metric in metrics), _ZERO)
    decision_count = sum(metric.decision_count for metric in metrics)
    return ReplayMetrics(
        currency=currency,
        outcome_manifest_sha256=combined_outcome_manifest_sha256(
            (
                observation.observation_id,
                observation.outcome_lineage.manifest_sha256,
            )
            for observation in observations
        ),
        demanded_units=demanded,
        filled_units=filled,
        backordered_units=demanded - filled,
        shortage_unit_days=sum(
            (metric.shortage_unit_days for metric in metrics),
            _ZERO,
        ),
        ending_inventory_units=sum(
            (metric.ending_inventory_units for metric in metrics),
            _ZERO,
        ),
        inventory_investment=sum(
            (metric.inventory_investment for metric in metrics),
            _ZERO,
        ),
        holding_cost=sum((metric.holding_cost for metric in metrics), _ZERO),
        ordering_cost=sum((metric.ordering_cost for metric in metrics), _ZERO),
        acquisition_cash=sum(
            (metric.acquisition_cash for metric in metrics),
            _ZERO,
        ),
        aog_risk_proxy_events=sum(
            (metric.aog_risk_proxy_events for metric in metrics),
            _ZERO,
        ),
        decision_count=decision_count,
        fill_rate=(
            Decimal("0")
            if decision_count == 0
            else (Decimal("1") if demanded == 0 else filled / demanded)
        ),
    )


def build_shadow_scorecard(
    request: ReplayEvaluationRequest,
) -> ShadowScorecard:
    """Aggregate a complete historical universe without any write capability."""

    grouped: dict[str, list[MatchedReplayObservation]] = defaultdict(list)
    cohort_by_id = {}
    for observation in request.observations:
        cohort_id = observation.cohort.cohort_id
        grouped[cohort_id].append(observation)
        cohort_by_id[cohort_id] = observation.cohort

    cohorts = []
    for cohort_id in sorted(grouped):
        observations = tuple(grouped[cohort_id])
        current = _aggregate(
            observations,
            side="current",
            currency=request.currency,
        )
        challenger = _aggregate(
            observations,
            side="challenger",
            currency=request.currency,
        )
        cohorts.append(
            ReplayCohortResult(
                cohort_id=cohort_id,
                cohort=cohort_by_id[cohort_id],
                observation_count=len(observations),
                observation_ids=tuple(
                    sorted(
                        observation.observation_id
                        for observation in observations
                    )
                ),
                current=current,
                challenger=challenger,
                delta=expected_metric_delta(current, challenger),
            )
        )

    current = _aggregate(
        request.observations,
        side="current",
        currency=request.currency,
    )
    challenger = _aggregate(
        request.observations,
        side="challenger",
        currency=request.currency,
    )
    lineage = tuple(
        ReplayObservationLineageRef(
            observation_id=observation.observation_id,
            decision_key=observation.decision_key,
            as_of=observation.as_of,
            horizon_end=observation.horizon_end,
            cohort_id=observation.cohort.cohort_id,
            source_snapshot_hash=(
                observation.current_lineage.source_snapshot_hash
            ),
            outcome_manifest_sha256=(
                observation.outcome_lineage.manifest_sha256
            ),
            current_planning_fingerprint=(
                observation.current_lineage.planning_fingerprint
            ),
            challenger_planning_fingerprint=(
                observation.challenger_lineage.planning_fingerprint
            ),
            current_request_sha256=(
                observation.current_lineage.planning_request_sha256
            ),
            challenger_request_sha256=(
                observation.challenger_lineage.planning_request_sha256
            ),
            current_planning_run_id=(
                observation.current_lineage.planning_run_id
            ),
            current_planning_selection_decision_key=(
                observation.current_lineage.planning_selection_decision_key
            ),
            challenger_planning_run_id=(
                observation.challenger_lineage.planning_run_id
            ),
            challenger_planning_selection_decision_key=(
                observation.challenger_lineage.planning_selection_decision_key
            ),
        )
        for observation in sorted(
            request.observations,
            key=lambda item: item.observation_id,
        )
    )
    snapshot_hashes = tuple(
        sorted({item.source_snapshot_hash for item in lineage})
    )
    fingerprints = tuple(
        sorted(
            {
                fingerprint
                for item in lineage
                for fingerprint in (
                    item.current_planning_fingerprint,
                    item.challenger_planning_fingerprint,
                )
            }
        )
    )
    exclusion_counts = Counter(
        exclusion.reason_code for exclusion in request.exclusions
    )
    return ShadowScorecard(
        tenant_id=request.tenant_id,
        currency=request.currency,
        universe_id=request.universe_id,
        universe_sha256=request.universe_sha256,
        universe_decisions=request.universe_decisions,
        current_policy_label=request.current_policy_label,
        challenger_policy_label=request.challenger_policy_label,
        comparison_rule=request.comparison_rule,
        comparison_rule_definition=COMPARISON_RULE_DEFINITIONS[
            request.comparison_rule
        ],
        match_tolerance=request.match_tolerance,
        observation_count=len(request.observations),
        total_observation_count=request.expected_decision_count,
        excluded_observation_count=len(request.exclusions),
        coverage_rate=(
            Decimal(len(request.observations))
            / Decimal(request.expected_decision_count)
        ),
        exclusions_by_reason=tuple(
            ReplayExclusionCount(reason_code=reason, count=count)
            for reason, count in sorted(exclusion_counts.items())
        ),
        exclusions=request.exclusions,
        current=current,
        challenger=challenger,
        delta=expected_metric_delta(current, challenger),
        cohorts=tuple(cohorts),
        metric_definitions=_METRIC_DEFINITIONS,
        observation_lineage=lineage,
        source_snapshot_hashes=snapshot_hashes,
        planning_fingerprints=fingerprints,
    )


__all__ = ["build_shadow_scorecard"]
