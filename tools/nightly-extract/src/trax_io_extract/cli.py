"""Trax IO Nightly Extract Utility — CLI.

Phase 2: real Oracle execution via ``python-oracledb`` (thin mode).

The CLI still supports ``--dry-run`` for local/offline smoke-testing,
which emits one empty ``[]`` placeholder per selected domain without
opening a database connection. ``--no-dry-run`` (the default) requires
Oracle connection env vars and executes each SQL for real.

S3 writes are **not** handled here; this phase is local-disk only.
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
from trax_io_extract.manifest import DomainArtifact, ExtractManifest
from trax_io_extract.oracle import (
    MissingOracleConfigError,
    OracleConnectionConfig,
    oracle_connection,
)
from trax_io_extract.runner import _compute_source_sql_sha256, run_extract

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
    help="Local output directory. Phase 2 writes JSON here; no S3 upload.",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=False,
    show_default=True,
    help="If set, skip Oracle execution and emit empty JSON placeholders.",
)
def extract(
    tenant_id: str,
    extract_date: datetime,
    window_days: int,
    demand_history_months: int,
    transaction: str | None,
    selected_domains: tuple[str, ...],
    output_dir: Path,
    dry_run: bool,
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

    run_id = str(ULID())
    run_dir = output_dir / f"extract_date={extract_date_value.isoformat()}" / f"run_id={run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

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
            run_dir=run_dir,
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

        manifest = run_extract(
            domains_to_run=to_run,
            sql_dir=SQL_DIR,
            output_dir=run_dir,
            bind_resolver=_bind_resolver,
            conn_factory=conn_factory,
            tenant_id=tenant_id,
            extract_date=extract_date_value,
            run_id=run_id,
        )

    n_ok = sum(1 for a in manifest.artifacts if a.status == "succeeded")
    click.echo(
        f"[trax-io-extract] tenant={tenant_id} date={extract_date_value.isoformat()} "
        f"run={run_id} domains={n_ok}/{len(manifest.artifacts)} status={manifest.run_status}"
    )


# Hookable at test time; see tests/test_cli_smoke.py.
def _resolve_conn_factory():
    return _default_conn_factory_from_env()


def _run_dry(
    *,
    to_run: list[Domain],
    run_dir: Path,
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

        out_path = run_dir / f"{domain.name}.json"
        payload = b"[]"
        out_path.write_bytes(payload)
        sha = hashlib.sha256(payload).hexdigest()

        artifacts.append(
            DomainArtifact(
                domain=domain.name,
                status="succeeded",
                s3_uri=None,
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
    (run_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


@main.command("list-domains", help="List the 21 canonical extract domains.")
def list_domains() -> None:
    for d in DOMAINS:
        flag = "windowed" if d.date_windowed else "snapshot"
        binds = ",".join(d.bind_vars) if d.bind_vars else "-"
        click.echo(f"{d.position:>2}  {d.name:<32}  {flag:<8}  binds={binds}")


if __name__ == "__main__":
    main()
