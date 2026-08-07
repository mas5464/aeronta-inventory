"""Structured operational events that survive the default log formatter."""

from __future__ import annotations

import json
import logging
from typing import Any


def log_operational_event(
    logger: logging.Logger,
    level: int,
    event: str,
    /,
    **fields: Any,
) -> None:
    """Emit one bounded JSON event and retain fields on the ``LogRecord``.

    Stdlib's default formatter renders only ``record.getMessage()`` and drops
    values supplied exclusively through ``extra``. Encoding the same safe
    dimensions into the message makes stdout/stderr logs directly collectable
    without requiring a vendor-specific metrics SDK, while ``extra`` preserves
    structured access for logging backends and contract tests.
    """

    payload = {"event": event, **fields}
    logger.log(
        level,
        json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        extra=payload,
    )


__all__ = ["log_operational_event"]
