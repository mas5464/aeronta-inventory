"""Fixtures: a real PartLocationContext from #11's extract sample + a demand-swapping helper."""

from __future__ import annotations

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
