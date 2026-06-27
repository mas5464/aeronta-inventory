"""Event lane end-to-end over #11's extract sample, materialized into online bundles."""

from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_feature_store.materialize import materialize_bundle
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.event_lane.events import DomainEvent, EventKind, RemovalRecordedPayload
from trax_io_spine.event_lane.handler import EventLaneHandler
from trax_io_spine.event_lane.online import InMemoryOnlineStore
from trax_io_spine.writeback.target import InMemoryWritebackTarget

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _online_store_from_sample(tenant_id: str):
    fs, _inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id=tenant_id)
    tenant = TenantContext(tenant_id=tid)
    bundles = [
        materialize_bundle(fs, tenant=tenant, pn=pn, location=loc) for pn, loc in keys
    ]
    return InMemoryOnlineStore(bundles), keys


def test_removal_event_recomputes_the_affected_key() -> None:
    store, keys = _online_store_from_sample("acme")
    pn, loc = keys[0]
    handler = EventLaneHandler(online_store=store, writeback=InMemoryWritebackTarget())
    ev = DomainEvent(
        tenant_id="acme", kind=EventKind.REMOVAL_RECORDED,
        occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload=RemovalRecordedPayload(pn=pn, tail="C-FABC", location=loc),
    )
    res = handler.handle(ev)
    total = res.summary["recommendations"]
    routed = (
        res.summary["written"] + res.summary["deferred"] + res.summary["failed"]
        + res.summary["queued"] + res.summary["rejected"]
    )
    assert routed == total  # every recommendation lands in exactly one bucket
    assert res.summary["skipped"] == 0  # the key's bundle has the required inputs


def test_cross_tenant_event_writes_nothing() -> None:
    store, keys = _online_store_from_sample("acme")
    pn, loc = keys[0]
    writeback = InMemoryWritebackTarget()
    handler = EventLaneHandler(online_store=store, writeback=writeback)
    ev = DomainEvent(
        tenant_id="other-airline", kind=EventKind.REMOVAL_RECORDED,
        occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload=RemovalRecordedPayload(pn=pn, tail="C-FABC", location=loc),
    )
    res = handler.handle(ev)  # no bundle for tenant other-airline -> empty
    assert res.summary["recommendations"] == 0
    assert writeback.history == []
