from pathlib import Path

import pytest
from trax_io_feature_store import TenantContext
from trax_io_feature_store.materialize import materialize_bundle
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.event_lane.online import InMemoryOnlineStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture
def online_sample():
    """(InMemoryOnlineStore, keys) materialized from #11's extract sample for tenant 'acme'."""
    fs, _inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    tenant = TenantContext(tenant_id=tid)
    bundles = [materialize_bundle(fs, tenant=tenant, pn=pn, location=loc) for pn, loc in keys]
    return InMemoryOnlineStore(bundles), keys
