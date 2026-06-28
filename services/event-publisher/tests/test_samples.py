import pytest

from trax_io_event_publisher.samples import make_event
from trax_io_event_publisher.schemas import EventKind


@pytest.mark.parametrize("kind", list(EventKind))
def test_make_event_is_valid_for_every_kind(kind):
    env = make_event(kind)
    assert env.kind == kind
    assert type(env.payload) is not None
    # round-trips through JSON without error
    type(env).model_validate_json(env.model_dump_json())


def test_make_event_accepts_string_kind():
    env = make_event("stock_moved")
    assert env.kind == EventKind.STOCK_MOVED


def test_overrides_apply():
    env = make_event(EventKind.STOCK_MOVED, tenant_id="other-air")
    assert env.tenant_id == "other-air"
