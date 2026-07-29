"""EmpiricalBayesProjector — a DemandProjector for the ULTRA_RARE regime.

Shrinks each part's sparse removal count toward a peer-group Gamma-Poisson prior and emits
the deterministic ULTRA_RARE COMPOUND_POISSON projection with the EB-shrunken lambda + a
widened (posterior-predictive) std. Every other regime delegates to the fallback projector.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import TYPE_CHECKING

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.basis import demand_basis_trace
from trax_io_reco.demand.projection import DemandProjectorProtocol, HistoricalScheduledProjector

from trax_io_forecasting.eb import posterior_predictive_var, posterior_rate
from trax_io_forecasting.peer_priors import PeerPriorProvider, peer_record_from_context

EMPIRICAL_BAYES_PROJECTOR_VERSION = "gamma-poisson-eb-v1"

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

class EmpiricalBayesProjector:
    def __init__(
        self,
        provider: PeerPriorProvider,
        fallback: DemandProjectorProtocol | None = None,
        *,
        basis_window_days: int | None = None,
    ) -> None:
        self._provider = provider
        self._fallback = fallback or HistoricalScheduledProjector(
            basis_window_days=basis_window_days
        )
        self._basis = basis_window_days

    def project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection:
        if regime is not Regime.ULTRA_RARE:
            return self._fallback.project(context=context, regime=regime)

        try:
            rec = peer_record_from_context(context, basis_window_days=self._basis)
            prior = self._provider.get_prior(
                ata_chapter=rec.ata_chapter,
                canonical_tier=rec.canonical_tier,
                part_class=rec.part_class,
            )
            lam_per_day = posterior_rate(prior, rec.count, rec.exposure)
            var_per_day = posterior_predictive_var(prior, rec.count, rec.exposure)

            trace = demand_basis_trace(context.demand_history)
            sched_total = float(sum(s.qty for s in context.scheduled_demand))
            by_aircraft: dict[str, float] = {}
            by_task: dict[str, float] = {}
            by_date: dict = {}
            for s in context.scheduled_demand:
                if s.ac_type:
                    by_aircraft[s.ac_type] = by_aircraft.get(s.ac_type, 0.0) + s.qty
                by_task[s.source_ref] = by_task.get(s.source_ref, 0.0) + s.qty
                by_date[s.due_date] = by_date.get(s.due_date, 0.0) + s.qty

            clump_p = (
                min(1.0, rec.count / trace.demanded_units)
                if rec.count > 0 and trace.demanded_units > 0
                else 1.0
            )
            mean_per_day = lam_per_day / clump_p
            compound_var_per_day = (
                var_per_day + lam_per_day * (1.0 - clump_p)
            ) / (clump_p**2)
            return DemandProjection(
                mean_per_day=mean_per_day,
                std_per_day=math.sqrt(compound_var_per_day),
                dist_kind="COMPOUND_POISSON",
                dist_params={"lambda": lam_per_day, "clump_p": clump_p},
                historical_component=mean_per_day,
                scheduled_component=0.0,
                scheduled_demand_total=sched_total,
                scheduled_by_date=by_date,
                by_aircraft=by_aircraft,
                by_task=by_task,
                basis_window_days=int(rec.exposure),
                forecast_model="gamma-poisson-empirical-bayes",
                forecast_version=EMPIRICAL_BAYES_PROJECTOR_VERSION,
            )
        except Exception:  # noqa: BLE001 - intentional resilience boundary: never break a batch
            return self._fallback.project(context=context, regime=regime)


def build_eb_projector(
    contexts: Iterable[PartLocationContext],
    fallback: DemandProjectorProtocol | None = None,
    *,
    basis_window_days: int | None = None,
    min_peers: int = 5,
) -> EmpiricalBayesProjector:
    """Pre-pass: fit the peer-prior provider from a batch of contexts, then build the projector."""
    records = [
        peer_record_from_context(c, basis_window_days=basis_window_days) for c in contexts
    ]
    provider = PeerPriorProvider.fit(records, min_peers=min_peers)
    return EmpiricalBayesProjector(
        provider, fallback=fallback, basis_window_days=basis_window_days
    )


__all__ = [
    "EMPIRICAL_BAYES_PROJECTOR_VERSION",
    "EmpiricalBayesProjector",
    "build_eb_projector",
]
