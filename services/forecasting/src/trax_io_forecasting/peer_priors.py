"""Peer-group empirical-Bayes priors with coarsening backoff (within-tenant, v1)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from trax_io_forecasting.eb import GammaPrior, fit_prior

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

_DEFAULT_BASIS_DAYS = 730


@dataclass(frozen=True)
class PeerRecord:
    ata_chapter: str | None
    canonical_tier: int
    part_class: str | None
    count: float
    exposure: float


def peer_record_from_context(
    context: PartLocationContext, *, basis_window_days: int = _DEFAULT_BASIS_DAYS
) -> PeerRecord:
    count = float(sum(o.removals + o.issues for o in context.demand_history.observations))
    return PeerRecord(
        ata_chapter=context.part_attributes.ata_chapter,
        canonical_tier=context.criticality.canonical_tier,
        part_class=context.part_attributes.part_class,
        count=count,
        exposure=float(basis_window_days),
    )


def _keys(ata: str | None, tier: int, cls: str | None) -> list[tuple]:
    # finest -> coarsest; global is the empty tuple
    return [(ata, tier, cls), (ata, tier), (tier,), ()]


@dataclass(frozen=True)
class PeerPriorProvider:
    _priors: dict[tuple, GammaPrior]
    _global: GammaPrior

    @classmethod
    def fit(cls, records: Iterable[PeerRecord], *, min_peers: int = 5) -> PeerPriorProvider:
        recs = list(records)
        groups: dict[tuple, list[PeerRecord]] = {}
        for r in recs:
            for key in _keys(r.ata_chapter, r.canonical_tier, r.part_class)[:-1]:
                groups.setdefault(key, []).append(r)
        priors = {
            key: fit_prior([m.count / m.exposure for m in members],
                           [m.exposure for m in members])
            for key, members in groups.items()
            if len(members) >= min_peers
        }
        global_prior = fit_prior(
            [r.count / r.exposure for r in recs], [r.exposure for r in recs]
        )
        return cls(_priors=priors, _global=global_prior)

    def get_prior(
        self, *, ata_chapter: str | None, canonical_tier: int, part_class: str | None
    ) -> GammaPrior:
        for key in _keys(ata_chapter, canonical_tier, part_class)[:-1]:
            prior = self._priors.get(key)
            if prior is not None:
                return prior
        return self._global
