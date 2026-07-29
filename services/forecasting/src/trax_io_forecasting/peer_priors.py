"""Peer-group empirical-Bayes priors with coarsening backoff (within-tenant, v1)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from trax_io_reco.demand.basis import demand_basis_trace

from trax_io_forecasting.eb import GammaPrior, fit_prior

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

@dataclass(frozen=True)
class PeerRecord:
    ata_chapter: str | None
    canonical_tier: int
    part_class: str | None
    count: float
    exposure: float


def peer_record_from_context(
    context: PartLocationContext, *, basis_window_days: int | None = None
) -> PeerRecord:
    trace = demand_basis_trace(context.demand_history)
    count = float(trace.demand_event_count or 0)
    exposure = float(trace.exposure_days)
    if (
        basis_window_days is not None
        and context.demand_history.observation_start is None
        and trace.exposure_days > 0
    ):
        # Backward-compatible explicit override for legacy history only. A
        # configured persisted interval is authoritative.
        exposure = float(basis_window_days)
    return PeerRecord(
        ata_chapter=context.part_attributes.ata_chapter,
        canonical_tier=context.criticality.canonical_tier,
        part_class=context.part_attributes.part_class,
        count=count,
        exposure=exposure,
    )


def _keys(ata: str | None, tier: int, cls: str | None) -> list[tuple]:
    # finest -> coarsest; global is the empty tuple
    return [(ata, tier, cls), (ata, tier), (tier,), ()]


@dataclass(frozen=True)
class PeerPriorProvider:
    _priors: dict[tuple, GammaPrior]
    _global: GammaPrior

    @staticmethod
    def _rates_and_exposures(members: list[PeerRecord]) -> tuple[list[float], list[float]]:
        usable = [m for m in members if m.exposure > 0.0]
        return [m.count / m.exposure for m in usable], [m.exposure for m in usable]

    @classmethod
    def fit(cls, records: Iterable[PeerRecord], *, min_peers: int = 5) -> PeerPriorProvider:
        recs = list(records)
        groups: dict[tuple, list[PeerRecord]] = {}
        for r in recs:
            for key in _keys(r.ata_chapter, r.canonical_tier, r.part_class)[:-1]:
                groups.setdefault(key, []).append(r)
        priors = {
            key: fit_prior(*cls._rates_and_exposures(members))
            for key, members in groups.items()
            if len(members) >= min_peers
        }
        global_prior = fit_prior(*cls._rates_and_exposures(recs))
        return cls(_priors=priors, _global=global_prior)

    def get_prior(
        self, *, ata_chapter: str | None, canonical_tier: int, part_class: str | None
    ) -> GammaPrior:
        for key in _keys(ata_chapter, canonical_tier, part_class)[:-1]:
            prior = self._priors.get(key)
            if prior is not None:
                return prior
        return self._global
