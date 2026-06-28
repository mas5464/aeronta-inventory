from trax_io_event_publisher import make_event

from trax_io_spine.event_lane.ingestor import EventIngestor, InMemoryDeadLetterSink
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _removal_for(pn, loc):
    base = make_event("removal_recorded", tenant_id="acme")
    return base.model_copy(
        update={"payload": base.payload.model_copy(update={"pn": pn, "location": loc})}
    )


def test_producer_oracle_events_drive_recompute_end_to_end(online_sample):
    # Build a JSONL feed the way the eMRO producer would emit it, using #3's oracle.
    store, keys = online_sample
    pn, loc = keys[0]
    good = _removal_for(pn, loc)
    feed = [
        good.model_dump_json(),                              # PROCESSED
        good.model_dump_json(),                              # DUPLICATE (same event_id)
        make_event("eo_published", tenant_id="acme").model_dump_json(),  # NO_OP (fan-out)
        "{ truncated",                                       # INVALID
    ]
    dlq = InMemoryDeadLetterSink()
    ing = EventIngestor(store, InMemoryWritebackTarget(), dlq=dlq)
    report = ing.ingest_batch(feed)

    assert (report.received, report.processed, report.duplicate, report.no_op, report.invalid) \
        == (4, 1, 1, 1, 1)
    assert report.recompute_totals["recommendations"] >= 2
    assert len(dlq.entries) == 1
    # every parsed line that adapted is one of our canonical events
    assert all(
        o.event_id is not None
        for o in report.outcomes
        if o.status.value != "invalid"
    )


def test_each_oracle_kind_round_trips_through_ingestor(online_sample):
    store, _keys = online_sample
    ing = EventIngestor(store, InMemoryWritebackTarget())
    for kind in ["flight_completed", "stock_moved", "wo_scheduled", "vendor_price_changed",
                 "plan_published", "removal_recorded", "eo_published"]:
        raw = make_event(kind, tenant_id="acme").model_dump_json()
        out = ing.ingest_raw(raw)
        assert out.status.value in {"processed", "no_op"}  # never invalid/duplicate here
        assert out.kind == kind  # the ingestor surfaced the right kind from the decoded event
