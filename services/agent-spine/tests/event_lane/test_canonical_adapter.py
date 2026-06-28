from pathlib import Path

from trax_io_event_publisher.samples import make_event
from trax_io_feature_store import TenantContext
from trax_io_feature_store.materialize import materialize_bundle
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.event_lane.canonical_adapter import to_domain_event
from trax_io_spine.event_lane.events import EventKind as SlimKind
from trax_io_spine.event_lane.handler import EventLaneHandler
from trax_io_spine.event_lane.online import InMemoryOnlineStore
from trax_io_spine.writeback.target import InMemoryWritebackTarget

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _online_store_from_sample(tenant_id: str):
    fs, _inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id=tenant_id)
    tenant = TenantContext(tenant_id=tid)
    bundles = [materialize_bundle(fs, tenant=tenant, pn=pn, location=loc) for pn, loc in keys]
    return InMemoryOnlineStore(bundles), keys


def test_stock_moved_down_projects():
    canonical = make_event("stock_moved", tenant_id="acme-air")
    slim = to_domain_event(canonical)
    assert slim.tenant_id == "acme-air"
    assert slim.kind == SlimKind.STOCK_MOVED
    assert slim.payload.pn == canonical.payload.pn
    assert slim.payload.from_location == canonical.payload.from_location
    assert slim.payload.to_location == canonical.payload.to_location
    assert slim.payload.qty == canonical.payload.qty
    assert slim.occurred_at == canonical.occurred_at


def test_every_kind_adapts_without_error():
    for kind in ["flight_completed", "stock_moved", "wo_scheduled",
                 "vendor_price_changed", "plan_published", "removal_recorded", "eo_published"]:
        slim = to_domain_event(make_event(kind))
        assert slim.kind.value == kind


def test_canonical_event_drives_the_handler_end_to_end():
    # spec §8: make_event -> to_domain_event -> EventLaneHandler.handle -> OrchestrationResult
    store, keys = _online_store_from_sample("acme")
    pn, loc = keys[0]
    handler = EventLaneHandler(online_store=store, writeback=InMemoryWritebackTarget())
    # a removal_recorded for a real (pn, location) resolves to that key and recomputes
    canonical = make_event(
        "removal_recorded", tenant_id="acme",
        payload=make_event("removal_recorded").payload.model_copy(
            update={"pn": pn, "location": loc}
        ),
    )
    res = handler.handle(to_domain_event(canonical))
    total = res.summary["recommendations"]
    routed = (
        res.summary["written"] + res.summary["deferred"] + res.summary["failed"]
        + res.summary["queued"] + res.summary["rejected"]
    )
    assert routed == total  # every recommendation lands in exactly one bucket
    assert res.summary["skipped"] == 0  # the key's bundle has the required inputs


def test_fan_out_kind_adapts_and_handles_without_error():
    # a flight_completed currently resolves to no keys -> handler no-ops cleanly
    store, _keys = _online_store_from_sample("acme")
    handler = EventLaneHandler(online_store=store, writeback=InMemoryWritebackTarget())
    res = handler.handle(to_domain_event(make_event("flight_completed", tenant_id="acme")))
    assert res.summary["recommendations"] == 0
