"""eMRO-side producer: at-least-once delivery with retry/backoff/dead-letter."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from trax_io_event_publisher.dlq import DeadLetterQueue, InMemoryDeadLetterQueue
from trax_io_event_publisher.schemas import EventEnvelope
from trax_io_event_publisher.transport import Transport, TransportError

_TERMINAL = {400, 401, 403}
_SUCCESS = {202, 409}


class PublishStatus(StrEnum):
    EMITTED = "emitted"
    REJECTED = "rejected"
    DEAD_LETTERED = "dead_lettered"


class PublishResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: PublishStatus
    attempts: int
    last_status_code: int | None
    dead_lettered: bool


class EventPublisher:
    def __init__(
        self,
        transport: Transport,
        *,
        dlq: DeadLetterQueue | None = None,
        max_attempts: int = 7,
        backoff_s: Sequence[float] = (1, 2, 4, 8, 16, 32, 60),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._dlq = dlq or InMemoryDeadLetterQueue()
        self._max_attempts = max_attempts
        self._backoff = tuple(backoff_s)
        self._sleep = sleep

    def publish(self, event: EventEnvelope) -> PublishResult:
        body = event.model_dump_json().encode("utf-8")
        last_code: int | None = None
        for attempt in range(1, self._max_attempts + 1):
            retry_after: float | None = None
            try:
                resp = self._transport.send(tenant_id=event.tenant_id, body=body)
                last_code = resp.status_code
                if resp.status_code in _SUCCESS:
                    return self._result(PublishStatus.EMITTED, attempt, last_code, False)
                if resp.status_code in _TERMINAL:
                    self._dlq.put(event, f"terminal {resp.status_code}")
                    return self._result(PublishStatus.REJECTED, attempt, last_code, True)
                if resp.status_code == 429:
                    retry_after = resp.retry_after_s
                # else: 5xx -> retryable
            except TransportError:
                last_code = None  # connection failure, no HTTP code
            if attempt < self._max_attempts:
                wait = retry_after if retry_after is not None else self._backoff_for(attempt)
                self._sleep(wait)
        self._dlq.put(event, f"exhausted {self._max_attempts} attempts")
        return self._result(PublishStatus.DEAD_LETTERED, self._max_attempts, last_code, True)

    def _backoff_for(self, attempt: int) -> float:
        idx = min(attempt - 1, len(self._backoff) - 1)
        return self._backoff[idx]

    @staticmethod
    def _result(
        status: PublishStatus, attempts: int, last_code: int | None, dead: bool
    ) -> PublishResult:
        return PublishResult(
            status=status, attempts=attempts, last_status_code=last_code, dead_lettered=dead
        )
