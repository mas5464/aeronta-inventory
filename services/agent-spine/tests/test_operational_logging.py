from __future__ import annotations

import io
import json
import logging

from trax_io_spine.operational_logging import log_operational_event


def test_operational_dimensions_survive_the_default_log_formatter() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger = logging.getLogger("trax_io_spine.test.operational")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    log_operational_event(
        logger,
        logging.INFO,
        "planning_worker_terminal",
        candidate_count=17,
        reconciliation="passed",
    )

    rendered = stream.getvalue().strip()
    payload = json.loads(rendered.split(":", maxsplit=2)[2])
    assert payload == {
        "candidate_count": 17,
        "event": "planning_worker_terminal",
        "reconciliation": "passed",
    }
