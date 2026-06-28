from trax_io_event_publisher import make_event

from trax_io_spine.event_lane.ingestor import (
    EventIngestor,
    IngestStatus,
    InMemoryDeadLetterSink,
)
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _removal_for(pn, loc):
    base = make_event("removal_recorded", tenant_id="acme")
    return base.model_copy(
        update={"payload": base.payload.model_copy(update={"pn": pn, "location": loc})}
    )


def test_malformed_json_is_invalid_and_dead_lettered(online_sample):
    store, _keys = online_sample
    dlq = InMemoryDeadLetterSink()
    ing = EventIngestor(store, InMemoryWritebackTarget(), dlq=dlq)
    out = ing.ingest_raw(b"{not valid json")
    assert out.status is IngestStatus.INVALID
    assert out.event_id is None
    assert out.reason is not None
    assert len(dlq.entries) == 1


def test_schema_invalid_event_is_invalid(online_sample):
    store, _keys = online_sample
    dlq = InMemoryDeadLetterSink()
    ing = EventIngestor(store, InMemoryWritebackTarget(), dlq=dlq)
    out = ing.ingest_raw(b'{"kind": "stock_moved"}')  # missing envelope/payload fields
    assert out.status is IngestStatus.INVALID
    assert len(dlq.entries) == 1


def test_ingest_batch_tallies_mixed_stream(online_sample):
    store, keys = online_sample
    pn, loc = keys[0]
    ing = EventIngestor(store, InMemoryWritebackTarget())
    good = _removal_for(pn, loc)
    items = [
        good.model_dump_json(),                       # processed
        good.model_dump_json(),                       # duplicate (same event_id)
        make_event("flight_completed", tenant_id="acme"),  # no_op (EventEnvelope, not raw)
        b"{garbage",                                  # invalid
    ]
    report = ing.ingest_batch(items)
    assert report.received == 4
    assert report.processed == 1
    assert report.duplicate == 1
    assert report.no_op == 1
    assert report.invalid == 1
    assert report.recompute_totals["recommendations"] >= 2
