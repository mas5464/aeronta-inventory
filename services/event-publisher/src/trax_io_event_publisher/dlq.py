"""Dead-letter sinks. In-memory for tests; S3 deferred to Phase 2."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from trax_io_event_publisher.schemas import EventEnvelope


@runtime_checkable
class DeadLetterQueue(Protocol):
    def put(self, event: EventEnvelope, reason: str) -> None: ...


class InMemoryDeadLetterQueue:
    def __init__(self) -> None:
        self.entries: list[tuple[EventEnvelope, str]] = []

    def put(self, event: EventEnvelope, reason: str) -> None:
        self.entries.append((event, reason))


class S3DeadLetterQueue:
    def put(self, event: EventEnvelope, reason: str) -> None:
        raise NotImplementedError("Phase 2: S3 dead-letter")
