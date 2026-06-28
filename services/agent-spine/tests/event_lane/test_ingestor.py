from trax_io_event_publisher import make_event

from trax_io_spine.event_lane.ingestor import EventIngestor, IngestStatus
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _removal_for(pn, loc):
    base = make_event("removal_recorded", tenant_id="acme")
    return base.model_copy(
        update={"payload": base.payload.model_copy(update={"pn": pn, "location": loc})}
    )


def test_event_hitting_a_real_key_is_processed(online_sample):
    store, keys = online_sample
    pn, loc = keys[0]  # ('FILTER-EXP-042', 'YYZ') -> recommendations: 2
    ing = EventIngestor(store, InMemoryWritebackTarget())
    out = ing.ingest(_removal_for(pn, loc))
    assert out.status is IngestStatus.PROCESSED
    assert out.kind == "removal_recorded"
    assert out.recompute["recommendations"] > 0


def test_fan_out_kind_is_no_op(online_sample):
    store, _keys = online_sample
    ing = EventIngestor(store, InMemoryWritebackTarget())
    out = ing.ingest(make_event("flight_completed", tenant_id="acme"))
    assert out.status is IngestStatus.NO_OP
    assert out.recompute["recommendations"] == 0


def test_cross_tenant_event_is_no_op(online_sample):
    store, keys = online_sample
    pn, loc = keys[0]
    ing = EventIngestor(store, InMemoryWritebackTarget())
    other = _removal_for(pn, loc).model_copy(update={"tenant_id": "other-air"})
    assert ing.ingest(other).status is IngestStatus.NO_OP


def test_same_event_id_twice_is_duplicate(online_sample):
    store, keys = online_sample
    pn, loc = keys[0]
    ing = EventIngestor(store, InMemoryWritebackTarget())
    ev = _removal_for(pn, loc)
    assert ing.ingest(ev).status is IngestStatus.PROCESSED
    dup = ing.ingest(ev)
    assert dup.status is IngestStatus.DUPLICATE
    assert dup.recompute is None
