import uuid

from trax_io_event_publisher.ids import is_uuid7, new_event_id


def test_new_event_id_is_uuid7():
    eid = new_event_id()
    assert is_uuid7(eid)
    assert uuid.UUID(eid).version == 7


def test_new_event_ids_are_unique():
    assert new_event_id() != new_event_id()


def test_is_uuid7_rejects_v4_and_garbage():
    assert is_uuid7(str(uuid.uuid4())) is False
    assert is_uuid7("not-a-uuid") is False
    assert is_uuid7("") is False
