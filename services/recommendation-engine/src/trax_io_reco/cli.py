"""`trax-io-reco` CLI (spec §8). Runs the recommendation service over a JSON demo/seed
file and prints the RecommendationBatch JSON — matching the repo's click convention.
"""

from __future__ import annotations

import json
from datetime import datetime

import click
from trax_io_feature_store import TenantContext

from trax_io_reco.data.demo_loader import build_stores
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.service import RecommendationService


@click.group()
def main() -> None:
    """Trax IO deterministic recommendation engine."""


@main.command()
@click.option(
    "--data-file",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON seed file (synthetic demo; see README for the schema).",
)
@click.option(
    "--extract-dir",
    default=None,
    type=click.Path(exists=True, file_okay=False),
    help="A nightly-extract output dir (21 <domain>.json + manifest.json) — real data.",
)
@click.option("--tenant", default=None, help="Tenant id (overrides the extract manifest).")
@click.option("--reporting-horizon", default=30, type=int, help="Reporting window (days).")
@click.option("--type", "type_filter", default=None, help="Filter to one recommendation type.")
@click.option("--now", default=None, help="ISO timestamp for deterministic output (default: now).")
@click.option(
    "--pool-by-part/--no-pool-by-part",
    default=False,
    help="Network-pool stock + demand across physical locations per PN "
    "(planning stays per pn x planning-location). Default off. "
    "Only applies with --extract-dir.",
)
def run(
    data_file: str | None,
    extract_dir: str | None,
    tenant: str | None,
    reporting_horizon: int,
    type_filter: str | None,
    now: str | None,
    pool_by_part: bool,
) -> None:
    """Generate recommendations from a seed file or a real extract dir; print JSON."""
    if bool(data_file) == bool(extract_dir):
        raise click.UsageError("provide exactly one of --data-file or --extract-dir")

    if extract_dir:
        fs, inv, tenant_id, keys = build_stores_from_extract(
            extract_dir, tenant_id=tenant, pool_by_part=pool_by_part
        )
    else:
        with open(data_file) as fh:  # type: ignore[arg-type]
            data = json.load(fh)
        fs, inv, tenant_id, keys = build_stores(data)
        tenant_id = tenant or tenant_id

    service = RecommendationService(feature_store=fs, inventory_state=inv)
    stamp = datetime.fromisoformat(now) if now else datetime.now()  # noqa: DTZ005
    batch = service.run(
        tenant=TenantContext(tenant_id=tenant_id),
        keys=keys,
        now=stamp,
        reporting_horizon_days=reporting_horizon,
    )

    if type_filter:
        kept = tuple(r for r in batch.recommendations if r.type.value == type_filter)
        batch = batch.model_copy(update={"recommendations": kept})

    click.echo(batch.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
