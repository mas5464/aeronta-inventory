from trax_io_event_publisher.dlq import InMemoryDeadLetterQueue
from trax_io_event_publisher.publisher import EventPublisher, PublishStatus
from trax_io_event_publisher.samples import make_event
from trax_io_event_publisher.transport import FakeTransport, TransportError, TransportResponse


def _recording_sleep():
    waits: list[float] = []
    return waits, waits.append


def test_202_is_emitted_first_try():
    pub = EventPublisher(FakeTransport([202]))
    res = pub.publish(make_event("stock_moved"))
    assert res.status is PublishStatus.EMITTED
    assert res.attempts == 1


def test_409_duplicate_is_idempotent_success():
    res = EventPublisher(FakeTransport([409])).publish(make_event("stock_moved"))
    assert res.status is PublishStatus.EMITTED


def test_400_is_terminal_no_retry_dead_letters():
    dlq = InMemoryDeadLetterQueue()
    t = FakeTransport([400])
    res = EventPublisher(t, dlq=dlq).publish(make_event("stock_moved"))
    assert res.status is PublishStatus.REJECTED
    assert res.attempts == 1
    assert len(t.sent) == 1
    assert len(dlq.entries) == 1


def test_5xx_retries_with_backoff_then_dead_letters():
    waits, sleep = _recording_sleep()
    dlq = InMemoryDeadLetterQueue()
    t = FakeTransport([500, 500, 500], default=500)  # always 5xx
    res = EventPublisher(
        t, dlq=dlq, max_attempts=3, backoff_s=(1, 2, 4), sleep=sleep
    ).publish(make_event("stock_moved"))
    assert res.status is PublishStatus.DEAD_LETTERED
    assert res.attempts == 3
    assert waits == [1, 2]  # slept before retries 2 and 3, not after the last
    assert len(dlq.entries) == 1


def test_transport_error_is_retryable_then_succeeds():
    waits, sleep = _recording_sleep()
    t = FakeTransport([TransportError("reset"), TransportResponse(status_code=202)])
    res = EventPublisher(t, max_attempts=3, backoff_s=(1, 2, 4), sleep=sleep).publish(
        make_event("stock_moved")
    )
    assert res.status is PublishStatus.EMITTED
    assert res.attempts == 2
    assert waits == [1]


def test_429_honors_retry_after_then_succeeds():
    waits, sleep = _recording_sleep()
    t = FakeTransport(
        [TransportResponse(status_code=429, retry_after_s=9.0), TransportResponse(status_code=202)]
    )
    res = EventPublisher(t, sleep=sleep).publish(make_event("stock_moved"))
    assert res.status is PublishStatus.EMITTED
    assert waits == [9.0]
