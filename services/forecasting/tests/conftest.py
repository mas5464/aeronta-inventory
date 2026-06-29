"""Fixtures: a real PartLocationContext from #11's extract sample + a demand-swapping helper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from trax_io_feature_store import TenantContext
from trax_io_feature_store.schemas import DemandHistory, DemandObservation
from trax_io_reco.contracts.context import PartLocationContext
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.data.feature_reader import FeatureReader

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture
def sample_context() -> PartLocationContext:
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    assembler = ContextAssembler(features=FeatureReader(fs), inventory_state=inv)
    pn, loc = keys[0]
    return assembler.assemble(tenant=TenantContext(tenant_id=tid), pn=pn, location=loc)


def with_demand(ctx: PartLocationContext, obs: list[DemandObservation]) -> PartLocationContext:
    history = DemandHistory(
        tenant_id=ctx.tenant_id, pn=ctx.pn, location=ctx.location, observations=tuple(obs),
        extract_date=ctx.demand_history.extract_date,
    )
    return ctx.model_copy(update={"demand_history": history})


@pytest.fixture(name="make_context")
def make_context_fixture():
    """Return the ``make_context`` factory so tests can inject it as a fixture."""
    return make_context


def make_context(
    *,
    ata_chapter: str | None = "32",
    canonical_tier: int = 1,
    part_class: str | None = "rotable",
    removals: list[int] | None = None,
) -> PartLocationContext:
    """Build a minimal PartLocationContext with the given peer-group attrs and demand history.

    Loads the shared extract-sample once and patches ``part_attributes``, ``criticality``, and
    ``demand_history`` so that EB projector tests can control the three peer-group knobs
    (``ata_chapter``, ``canonical_tier``, ``part_class``) plus a monthly ``removals`` list
    without duplicating fixture boilerplate.
    """
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    assembler = ContextAssembler(features=FeatureReader(fs), inventory_state=inv)
    pn, loc = keys[0]
    ctx = assembler.assemble(tenant=TenantContext(tenant_id=tid), pn=pn, location=loc)

    # Patch part_attributes: only override ata_chapter + part_class; keep the rest intact.
    new_attrs = ctx.part_attributes.model_copy(
        update={"ata_chapter": ata_chapter, "part_class": part_class}
    )
    # Patch criticality: override canonical_tier; keep raw_essentiality_code as-is.
    new_crit = ctx.criticality.model_copy(update={"canonical_tier": canonical_tier})

    # Build monthly DemandObservation list from the removals list.
    obs: list[DemandObservation] = []
    for i, r in enumerate(removals or []):
        obs.append(
            DemandObservation(
                bucket="month",
                period_start=date(2024 + (i // 12), (i % 12) + 1, 1),
                removals=r,
                issues=0,
            )
        )
    new_history = DemandHistory(
        tenant_id=ctx.tenant_id,
        pn=ctx.pn,
        location=ctx.location,
        observations=tuple(obs),
        extract_date=ctx.demand_history.extract_date,
    )

    return ctx.model_copy(
        update={
            "part_attributes": new_attrs,
            "criticality": new_crit,
            "demand_history": new_history,
        }
    )
