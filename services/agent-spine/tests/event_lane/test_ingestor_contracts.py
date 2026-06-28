from trax_io_spine.event_lane.ingestor import (
    IngestOutcome,
    IngestReport,
    IngestStatus,
    InMemoryDeadLetterSink,
)


def _out(status, recompute=None, event_id="e", kind="stock_moved", reason=None):
    return IngestOutcome(
        status=status, event_id=event_id, kind=kind, recompute=recompute, reason=reason
    )


def test_from_outcomes_tallies_counts_and_sums_recompute():
    outcomes = [
        _out(IngestStatus.PROCESSED, {"recommendations": 2, "written": 1, "deferred": 0,
             "failed": 0, "queued": 1, "rejected": 0, "skipped": 0}),
        _out(IngestStatus.NO_OP, {"recommendations": 0, "written": 0, "deferred": 0,
             "failed": 0, "queued": 0, "rejected": 0, "skipped": 0}),
        _out(IngestStatus.DUPLICATE),
        _out(IngestStatus.INVALID, event_id=None, kind=None, reason="bad"),
    ]
    report = IngestReport.from_outcomes(outcomes)
    assert (report.received, report.processed, report.no_op, report.duplicate, report.invalid) \
        == (4, 1, 1, 1, 1)
    assert report.recompute_totals["recommendations"] == 2
    assert report.recompute_totals["written"] == 1
    assert report.recompute_totals["skipped"] == 0
    assert len(report.outcomes) == 4


def test_from_outcomes_empty():
    report = IngestReport.from_outcomes([])
    assert report.received == 0
    assert report.recompute_totals == {
        "recommendations": 0, "written": 0, "deferred": 0, "failed": 0,
        "queued": 0, "rejected": 0, "skipped": 0,
    }


def test_dead_letter_sink_records():
    sink = InMemoryDeadLetterSink()
    sink.put("{bad json", "1 validation error")
    assert sink.entries == [("{bad json", "1 validation error")]
