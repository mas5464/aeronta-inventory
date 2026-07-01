"""Trax IO Nightly Extract Utility — CLI.

Real Oracle execution via ``python-oracledb`` (thin mode), with pluggable landing:
local disk by default (``--output-dir``) or S3 via ``--landing s3://bucket[/prefix]``
with optional SSE-KMS (``--kms-key-id``). ``boto3`` is imported lazily, only on the S3
branch, so the local path and the test suite never require it.

``--dry-run`` skips the database connection and emits one empty ``[]`` placeholder per
selected domain (it honors ``--landing`` too); ``--no-dry-run`` (the default) requires the
Oracle connection env vars and executes each SQL for real.
"""

from __future__ import annotations

import hashlib
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import click
from ulid import ULID

from trax_io_extract import __version__
from trax_io_extract.binds import resolve_binds
from trax_io_extract.domains import DOMAINS, DOMAINS_BY_NAME, Domain
from trax_io_extract.landing import LandingSink, LocalFsSink, S3Sink, landing_prefix
from trax_io_extract.manifest import DomainArtifact, ExtractManifest
from trax_io_extract.oracle import (
    MissingOracleConfigError,
    OracleConnectionConfig,
    oracle_connection,
)
from trax_io_extract.runner import _compute_source_sql_sha256, run_extract
from trax_io_extract.scope import (
    ExtractScope,
    resolve_scope,
    resolve_scope_planning_active,
)

PRODUCT_NAME = "Trax IO"
SQL_DIR = Path(__file__).resolve().parent.parent.parent / "sql"


def _default_conn_factory_from_env():  # pragma: no cover - exercised via monkeypatch
    cfg = OracleConnectionConfig.from_env()

    @contextmanager
    def factory() -> Iterator[Any]:
        with oracle_connection(cfg) as conn:
            yield conn

    return factory


@click.group(help=f"{PRODUCT_NAME} Nightly Extract Utility.")
@click.version_option(__version__, prog_name="trax-io-extract")
def main() -> None:
    """Top-level CLI entrypoint."""


@main.command(help="Run the nightly extract against the configured Oracle database.")
@click.option("--tenant-id", required=True, help="Tenant identifier (per-tenant isolation key).")
@click.option(
    "--extract-date",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Logical nightly date in YYYY-MM-DD (UTC).",
)
@click.option(
    "--window-days",
    type=int,
    default=90,
    show_default=True,
    help="Causal-values lookback window in days.",
)
@click.option(
    "--demand-history-months",
    type=int,
    default=36,
    show_default=True,
    help="Demand-history lookback window in months (rotables and expendables).",
)
@click.option(
    "--transaction",
    "transaction",
    type=str,
    default=None,
    help="Transaction code for the `events` domain (required unless `events` is skipped).",
)
@click.option(
    "--domain",
    "selected_domains",
    multiple=True,
    help="Repeatable. Limit the run to this subset of domain names (default: all 21).",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("./out"),
    show_default=True,
    help="Local landing directory (used unless --landing is an s3:// URI).",
)
@click.option(
    "--landing",
    default=None,
    help="Landing target. An 's3://bucket[/prefix]' URI lands to S3; otherwise local --output-dir.",
)
@click.option(
    "--kms-key-id",
    default=None,
    help="Per-tenant KMS key id/ARN for SSE-KMS on S3 PUTs (sub-project #9 exports this).",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=False,
    show_default=True,
    help="If set, skip Oracle execution and emit empty JSON placeholders.",
)
@click.option(
    "--scope-location",
    default=None,
    help=(
        "Limit the run to this station's planning-active parts (capped by "
        "--scope-max-parts) instead of the whole network. Absent, the run is unscoped "
        "(unchanged behavior)."
    ),
)
@click.option(
    "--scope-max-parts",
    type=int,
    default=500,
    show_default=True,
    help=(
        "Max number of parts to pull when --scope-location or "
        "--scope-planning-active is set. IN-list binds are chunked past 1000, "
        "so this is not itself capped at 1000."
    ),
)
@click.option(
    "--scope-planning-active",
    is_flag=True,
    default=False,
    help=(
        "Scope the run to all planning-active parts network-wide (across every "
        "station), capped by --scope-max-parts (e.g. --scope-max-parts=100000 for "
        "the full ~62K network). Mutually exclusive with --scope-location."
    ),
)
def extract(
    tenant_id: str,
    extract_date: datetime,
    window_days: int,
    demand_history_months: int,
    transaction: str | None,
    selected_domains: tuple[str, ...],
    output_dir: Path,
    landing: str | None,
    kms_key_id: str | None,
    dry_run: bool,
    scope_location: str | None,
    scope_max_parts: int,
    scope_planning_active: bool,
) -> None:
    extract_date_value: date = extract_date.date()

    if selected_domains:
        unknown = [n for n in selected_domains if n not in DOMAINS_BY_NAME]
        if unknown:
            raise click.BadParameter(f"unknown domain(s): {', '.join(unknown)}")
        to_run: list[Domain] = [DOMAINS_BY_NAME[n] for n in selected_domains]
    else:
        to_run = list(DOMAINS)

    if any(d.name == "events" for d in to_run) and transaction is None:
        raise click.BadParameter(
            "--transaction is required when the `events` domain is in the run "
            "(or exclude it via --domain)."
        )

    if scope_location and scope_planning_active:
        raise click.BadParameter(
            "--scope-location and --scope-planning-active are mutually exclusive; "
            "pass one or the other."
        )

    run_id = str(ULID())
    prefix = landing_prefix(extract_date_value, run_id)
    sink, landing_desc = _build_sink(
        landing=landing, output_dir=output_dir, prefix=prefix, kms_key_id=kms_key_id
    )

    def _bind_resolver(domain: Domain) -> dict[str, Any]:
        return resolve_binds(
            domain,
            extract_date=extract_date_value,
            window_days=window_days,
            demand_history_months=demand_history_months,
            transaction=transaction,
        )

    if dry_run:
        manifest = _run_dry(
            to_run=to_run,
            sink=sink,
            bind_resolver=_bind_resolver,
            tenant_id=tenant_id,
            extract_date_value=extract_date_value,
            run_id=run_id,
        )
    else:
        try:
            conn_factory = _resolve_conn_factory()
        except MissingOracleConfigError as exc:
            click.echo(
                f"[{PRODUCT_NAME}] ERROR: {exc}. Set the missing env var(s) or re-run with --dry-run.",
                err=True,
            )
            sys.exit(2)

        scope: ExtractScope | None = None
        if scope_location:
            with conn_factory() as conn:
                scope = resolve_scope(
                    conn, location=scope_location, max_parts=scope_max_parts
                )
            click.echo(
                f"[{PRODUCT_NAME}] scope location={scope.location} "
                f"parts={len(scope.parts)}/{scope_max_parts}",
                err=True,
            )
        elif scope_planning_active:
            with conn_factory() as conn:
                scope = resolve_scope_planning_active(conn, max_parts=scope_max_parts)
            click.echo(
                f"[{PRODUCT_NAME}] scope location=network-wide "
                f"parts={len(scope.parts)}/{scope_max_parts}",
                err=True,
            )

        manifest = run_extract(
            domains_to_run=to_run,
            sql_dir=SQL_DIR,
            sink=sink,
            bind_resolver=_bind_resolver,
            conn_factory=conn_factory,
            tenant_id=tenant_id,
            extract_date=extract_date_value,
            run_id=run_id,
            scope=scope,
        )

    n_ok = sum(1 for a in manifest.artifacts if a.status == "succeeded")
    click.echo(
        f"[trax-io-extract] tenant={tenant_id} date={extract_date_value.isoformat()} "
        f"run={run_id} landing={landing_desc} domains={n_ok}/{len(manifest.artifacts)} "
        f"status={manifest.run_status}"
    )


def _s3_bucket_and_prefix(landing: str, prefix: str) -> tuple[str, str]:
    """Parse 's3://bucket[/base]' + the run prefix into (bucket, full_key_prefix). Pure."""
    bucket, _, base = landing[len("s3://"):].partition("/")
    if not bucket:
        raise click.BadParameter(f"--landing must be 's3://bucket[/prefix]', got: {landing}")
    full_prefix = f"{base.strip('/')}/{prefix}" if base.strip("/") else prefix
    return bucket, full_prefix


def _build_sink(
    *, landing: str | None, output_dir: Path, prefix: str, kms_key_id: str | None
) -> tuple[LandingSink, str]:
    """Construct the landing sink and a human-readable destination string."""
    if landing and landing.startswith("s3://"):
        bucket, full_prefix = _s3_bucket_and_prefix(landing, prefix)
        import boto3  # lazy: boto3 only needed for the S3 path

        sink: LandingSink = S3Sink(
            boto3.client("s3"), bucket, prefix=full_prefix, sse_kms_key_id=kms_key_id
        )
        return sink, f"s3://{bucket}/{full_prefix}"
    run_dir = output_dir / prefix
    return LocalFsSink(run_dir), str(run_dir)


# Hookable at test time; see tests/test_cli_smoke.py.
def _resolve_conn_factory():
    return _default_conn_factory_from_env()


def _run_dry(
    *,
    to_run: list[Domain],
    sink: LandingSink,
    bind_resolver,
    tenant_id: str,
    extract_date_value: date,
    run_id: str,
) -> ExtractManifest:
    """Phase-1 behavior: empty placeholders, no DB connection."""
    started_at = datetime.now(timezone.utc)
    source_sql_sha256 = _compute_source_sql_sha256(SQL_DIR)

    artifacts: list[DomainArtifact] = []
    for domain in to_run:
        d_started = datetime.now(timezone.utc)
        raw_binds = bind_resolver(domain)
        serialized = {
            k: v.isoformat() if hasattr(v, "isoformat") else str(v)
            for k, v in raw_binds.items()
        }

        sql_path = SQL_DIR / domain.sql_file
        if not sql_path.is_file():
            raise click.ClickException(
                f"missing SQL file for domain {domain.name}: {sql_path}"
            )
        click.echo(
            f"[{PRODUCT_NAME}] domain={domain.name} sql={sql_path.name} binds={serialized}",
            err=True,
        )

        payload = b"[]"
        uri = sink.write(f"{domain.name}.json", payload)
        sha = hashlib.sha256(payload).hexdigest()

        artifacts.append(
            DomainArtifact(
                domain=domain.name,
                status="succeeded",
                s3_uri=uri,
                row_count=0,
                sha256=sha,
                bytes=len(payload),
                bind_vars=serialized,
                started_at=d_started,
                finished_at=datetime.now(timezone.utc),
            )
        )

    finished_at = datetime.now(timezone.utc)
    manifest = ExtractManifest.from_artifacts(
        tenant_id=tenant_id,
        extract_date=extract_date_value,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        source_sql_sha256=source_sql_sha256,
        extract_utility_version=__version__,
        artifacts=artifacts,
    )
    sink.write("manifest.json", manifest.model_dump_json(indent=2).encode("utf-8"))
    return manifest


@main.command("list-domains", help="List the 21 canonical extract domains.")
def list_domains() -> None:
    for d in DOMAINS:
        flag = "windowed" if d.date_windowed else "snapshot"
        binds = ",".join(d.bind_vars) if d.bind_vars else "-"
        click.echo(f"{d.position:>2}  {d.name:<32}  {flag:<8}  binds={binds}")


if __name__ == "__main__":
    main()
