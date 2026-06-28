from trax_io_event_publisher.samples import make_event

from trax_io_spine.event_lane.canonical_adapter import to_domain_event
from trax_io_spine.event_lane.events import EventKind as SlimKind


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
