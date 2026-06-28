"""Consumer-side event ingestion: canonical events -> adapt -> recompute -> report."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

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
