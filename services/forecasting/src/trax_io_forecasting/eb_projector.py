"""EmpiricalBayesProjector — a DemandProjector for the ULTRA_RARE regime.

Shrinks each part's sparse removal count toward a peer-group Gamma-Poisson prior and emits
the deterministic ULTRA_RARE COMPOUND_POISSON projection with the EB-shrunken lambda + a
widened (posterior-predictive) std. Every other regime delegates to the fallback projector.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.projection import DemandProjectorProtocol, HistoricalScheduledProjector

from trax_io_forecasting.eb import posterior_predictive_var, posterior_rate
from trax_io_forecasting.peer_priors import PeerPriorProvider, peer_record_from_context

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

_DEFAULT_BASIS_DAYS = 730


class EmpiricalBayesProjector:
    def __init__(
        self,
        provider: PeerPriorProvider,
        fallback: DemandProjectorProtocol | None = None,
        *,
        basis_window_days: int = _DEFAULT_BASIS_DAYS,
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

            sched_total = float(sum(s.qty for s in context.scheduled_demand))
            scheduled_per_day = sched_total / self._basis
            by_aircraft: dict[str, float] = {}
            by_task: dict[str, float] = {}
            for s in context.scheduled_demand:
                if s.ac_type:
                    by_aircraft[s.ac_type] = by_aircraft.get(s.ac_type, 0.0) + s.qty
                by_task[s.source_ref] = by_task.get(s.source_ref, 0.0) + s.qty

            mean_per_day = lam_per_day + scheduled_per_day
            return DemandProjection(
                mean_per_day=mean_per_day,
                std_per_day=math.sqrt(var_per_day),
                dist_kind="COMPOUND_POISSON",
                dist_params={"lambda": lam_per_day, "clump_p": 1.0},
                historical_component=lam_per_day,
                scheduled_component=scheduled_per_day,
                by_aircraft=by_aircraft,
                by_task=by_task,
                basis_window_days=self._basis,
            )
        except Exception:  # noqa: BLE001 - intentional resilience boundary: never break a batch
            return self._fallback.project(context=context, regime=regime)
