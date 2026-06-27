from datetime import UTC, date, datetime

import pytest
from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import FeatureBundle, StockPosition

from trax_io_spine.event_lane.events import DomainEvent, EventKind
from trax_io_spine.event_lane.online import InMemoryOnlineStore

ACME = TenantContext(tenant_id="acme")
_D = date(2026, 4, 1)


def _bundle(loc: str) -> FeatureBundle:
    return FeatureBundle(
        tenant_id="acme", pn="PN-A", location=loc,
        stock_position=StockPosition(tenant_id="acme", pn="PN-A", location=loc,
                                     on_hand=5, serviceable=5, extract_date=_D),
    )


def test_in_memory_online_store_get_and_miss() -> None:
    store = InMemoryOnlineStore([_bundle("LOC-1")])
    assert store.get_bundle(tenant=ACME, pn="PN-A", location="LOC-1").location == "LOC-1"
    with pytest.raises(FeatureStoreLookupError):
        store.get_bundle(tenant=ACME, pn="PN-A", location="NOPE")
    with pytest.raises(FeatureStoreLookupError):
        store.get_bundle(tenant=TenantContext(tenant_id="other"), pn="PN-A", location="LOC-1")


def test_handler_empty_keys_returns_empty_result() -> None:
    from trax_io_spine.event_lane.handler import EventLaneHandler
    from trax_io_spine.writeback.target import InMemoryWritebackTarget

    handler = EventLaneHandler(
        online_store=InMemoryOnlineStore([_bundle("LOC-1")]),
        writeback=InMemoryWritebackTarget(),
    )
    # eo_published is a fan-out event -> DirectKeyResolver returns empty -> no recompute
    from trax_io_spine.event_lane.events import EoPublishedPayload

    ev = DomainEvent(
        tenant_id="acme", kind=EventKind.EO_PUBLISHED, occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload=EoPublishedPayload(eo_number="EO-1", ata_chapter="32", affected_fleet="A320"),
    )
    res = handler.handle(ev)
    assert res.summary["recommendations"] == 0
    assert res.written == () and res.queued == ()
