from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.service import RecommendationService

from trax_io_forecasting.gb_projector import GradientBoostedProjector

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def test_gradient_boosted_projector_drives_the_engine():
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    svc = RecommendationService(
        feature_store=fs, inventory_state=inv, projector=GradientBoostedProjector()
    )
    batch = svc.run(
        tenant=TenantContext(tenant_id=tid), keys=keys, now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    assert batch.summary.total >= 0  # the injected projector runs end to end without error
