"""Service-role import for fully validated trusted replay-universe packages."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from trax_io_reco.contracts.replay import ReplayEvaluationRequest

from .replay import ReplayUniverseRecord, seed_replay_universe

MAX_REPLAY_IMPORT_BYTES = 512 * 1024 * 1024


def import_replay_universe(
    pool,
    *,
    tenant_uuid: str,
    universe_ref: str,
    input_path: str | Path,
) -> ReplayUniverseRecord:
    """Validate an operator/data-pipeline package, then idempotently seal it."""

    path = Path(input_path)
    stat = path.stat()
    if not path.is_file() or stat.st_size <= 0:
        raise ValueError("trusted replay import must be a non-empty regular file")
    if stat.st_size > MAX_REPLAY_IMPORT_BYTES:
        raise ValueError("trusted replay import exceeds the 512 MiB safety limit")

    with pool.connection() as conn:
        role = conn.execute(
            """
            select current_user, rolbypassrls
            from pg_roles
            where rolname = current_user
            """
        ).fetchone()
    if role is None or role[1] is not True:
        raise PermissionError(
            "trusted replay import requires the service seed role"
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("trusted replay import is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("trusted replay import root must be a JSON object")
    request = ReplayEvaluationRequest.model_validate(payload)
    return seed_replay_universe(
        pool,
        tenant_uuid=tenant_uuid,
        universe_ref=universe_ref,
        request=request,
    )


def main() -> None:
    from .db import make_pool

    parser = argparse.ArgumentParser(prog="trax-io-replay-import")
    parser.add_argument(
        "--database-url",
        default=None,
        help="trax_seed/service-role PostgreSQL URL",
    )
    parser.add_argument("--tenant-uuid", required=True)
    parser.add_argument("--universe-ref", required=True)
    parser.add_argument("--input", required=True, help="ReplayEvaluationRequest JSON")
    args = parser.parse_args()

    database_url = (
        args.database_url
        or os.environ.get("WORKER_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not database_url:
        parser.error(
            "WORKER_DATABASE_URL or DATABASE_URL is required "
            "(--database-url is local-development only)"
        )
    try:
        pool = make_pool(database_url)
        try:
            record = import_replay_universe(
                pool,
                tenant_uuid=args.tenant_uuid,
                universe_ref=args.universe_ref,
                input_path=args.input,
            )
            print(record.model_dump_json())
        finally:
            pool.close()
    except Exception:  # noqa: BLE001 - CLI boundary must redact package/DB facts
        parser.exit(
            status=1,
            message=(
                "trax-io-replay-import: error: trusted replay import failed; "
                "review the controlled operator diagnostics\n"
            ),
        )


if __name__ == "__main__":
    main()
