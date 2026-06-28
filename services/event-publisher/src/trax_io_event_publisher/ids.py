"""UUIDv7 event-id helpers (stdlib uuid.uuid7, available on Python 3.14)."""

from __future__ import annotations

import uuid


def new_event_id() -> str:
    return str(uuid.uuid7())


def is_uuid7(value: str) -> bool:
    try:
        return uuid.UUID(value).version == 7
    except (ValueError, AttributeError, TypeError):
        return False
