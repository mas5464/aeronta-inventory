"""trax-io-publisher CLI — emit canonical events (stdout) or through the fake endpoint."""

from __future__ import annotations

import argparse

from trax_io_event_publisher.samples import make_event
from trax_io_event_publisher.schemas import EventKind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trax-io-publisher")
    sub = parser.add_subparsers(dest="cmd", required=True)
    emit = sub.add_parser("emit", help="emit a sample canonical event")
    emit.add_argument("--kind", required=True, choices=[k.value for k in EventKind])
    emit.add_argument("--tenant", required=True)
    emit.add_argument("--to", default="stdout", choices=["stdout", "fake"])
    args = parser.parse_args(argv)

    event = make_event(args.kind, tenant_id=args.tenant)
    if args.to == "stdout":
        print(event.model_dump_json(indent=2))
        return 0

    from trax_io_event_publisher.endpoint import create_app
    from trax_io_event_publisher.publisher import EventPublisher
    from trax_io_event_publisher.transport import AsgiTransport

    result = EventPublisher(AsgiTransport(create_app())).publish(event)
    print(f"{result.status.value} (attempts={result.attempts})")
    return 0
