"""Consumer-side event ingestion: canonical events -> adapt -> recompute -> report."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict
from trax_io_event_publisher import EventEnvelope

from trax_io_spine.event_lane.canonical_adapter import to_domain_event
from trax_io_spine.event_lane.handler import EventLaneHandler
from trax_io_spine.event_lane.keys import KeyResolver
from trax_io_spine.event_lane.online import OnlineStore
from trax_io_spine.writeback.target import WritebackTarget

_SUMMARY_KEYS = (
    "recommendations", "written", "deferred", "failed", "queued", "rejected", "skipped",
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IngestStatus(StrEnum):
    PROCESSED = "processed"
    NO_OP = "no_op"
    DUPLICATE = "duplicate"
    INVALID = "invalid"


class IngestOutcome(_Frozen):
    status: IngestStatus
    event_id: str | None
    kind: str | None
    recompute: dict[str, int] | None
    reason: str | None = None


class IngestReport(_Frozen):
    received: int
    processed: int
    no_op: int
    duplicate: int
    invalid: int
    recompute_totals: dict[str, int]
    outcomes: tuple[IngestOutcome, ...]

    @classmethod
    def from_outcomes(cls, outcomes: Sequence[IngestOutcome]) -> IngestReport:
        outcomes = tuple(outcomes)
        totals = dict.fromkeys(_SUMMARY_KEYS, 0)
        for o in outcomes:
            if o.recompute:
                for k in _SUMMARY_KEYS:
                    totals[k] += o.recompute.get(k, 0)
        counts = Counter(o.status for o in outcomes)
        return cls(
            received=len(outcomes),
            processed=counts[IngestStatus.PROCESSED],
            no_op=counts[IngestStatus.NO_OP],
            duplicate=counts[IngestStatus.DUPLICATE],
            invalid=counts[IngestStatus.INVALID],
            recompute_totals=totals,
            outcomes=outcomes,
        )


@runtime_checkable
class DeadLetterSink(Protocol):
    def put(self, raw: str, reason: str) -> None: ...


class InMemoryDeadLetterSink:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def put(self, raw: str, reason: str) -> None:
        self.entries.append((raw, reason))


class EventIngestor:
    def __init__(
        self,
        online_store: OnlineStore,
        writeback: WritebackTarget,
        *,
        resolver: KeyResolver | None = None,
        dlq: DeadLetterSink | None = None,
        seen: set[str] | None = None,
    ) -> None:
        self._handler = EventLaneHandler(online_store, writeback, resolver=resolver)
        self._dlq = dlq or InMemoryDeadLetterSink()
        self._seen: set[str] = seen if seen is not None else set()

    def ingest(self, event: EventEnvelope) -> IngestOutcome:
        if event.event_id in self._seen:
            return IngestOutcome(
                status=IngestStatus.DUPLICATE, event_id=event.event_id,
                kind=event.kind.value, recompute=None,
            )
        self._seen.add(event.event_id)
        result = self._handler.handle(to_domain_event(event))
        summary = dict(result.summary)
        status = (
            IngestStatus.PROCESSED
            if summary.get("recommendations", 0) > 0
            else IngestStatus.NO_OP
        )
        return IngestOutcome(
            status=status, event_id=event.event_id, kind=event.kind.value, recompute=summary,
        )
