"""`trax-io-spine` CLI — offline end-to-end orchestration over an extract dir."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer
from trax_io_feature_store import TenantContext
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.supervisor import Supervisor
from trax_io_spine.writeback.rest import RestWritebackClient
from trax_io_spine.writeback.target import InMemoryWritebackTarget

app = typer.Typer(help="Trax IO Agent Spine — deterministic orchestration CLI.")


@app.command(name="run")
def run(
    extract_dir: str = typer.Option(..., "--extract-dir"),
    tenant: str = typer.Option(..., "--tenant"),
    now: str | None = typer.Option(None, "--now", help="ISO timestamp; defaults to now (UTC)"),
    apply: bool = typer.Option(False, "--apply/--dry-run"),
    shadow: bool = typer.Option(False, "--shadow/--no-shadow"),
    writeback_url: str = typer.Option("http://localhost:9000", "--writeback-url"),
) -> None:
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir, tenant_id=tenant)
    stamp = datetime.fromisoformat(now) if now else datetime.now(UTC)
    target = RestWritebackClient(writeback_url) if apply else InMemoryWritebackTarget()
    supervisor = Supervisor(feature_store=fs, inventory_state=inv, writeback=target, shadow=shadow)
    result = supervisor.run(tenant=TenantContext(tenant_id=tenant_id), keys=keys, now=stamp)
    typer.echo(json.dumps(result.summary))


@app.command(name="ingest")
def ingest(
    extract_dir: str = typer.Option(..., "--extract-dir"),
    tenant: str = typer.Option(..., "--tenant"),
    events: str = typer.Option(..., "--events", help="JSONL of canonical events"),
    apply: bool = typer.Option(False, "--apply/--dry-run"),
    writeback_url: str = typer.Option("http://localhost:9000", "--writeback-url"),
) -> None:
    from pathlib import Path  # noqa: PLC0415

    from trax_io_feature_store.materialize import materialize_bundle  # noqa: PLC0415

    from trax_io_spine.event_lane.ingestor import EventIngestor  # noqa: PLC0415
    from trax_io_spine.event_lane.online import InMemoryOnlineStore  # noqa: PLC0415

    fs, _inv, tenant_id, keys = build_stores_from_extract(extract_dir, tenant_id=tenant)
    tctx = TenantContext(tenant_id=tenant_id)
    bundles = [materialize_bundle(fs, tenant=tctx, pn=pn, location=loc) for pn, loc in keys]
    target = RestWritebackClient(writeback_url) if apply else InMemoryWritebackTarget()
    ingestor = EventIngestor(InMemoryOnlineStore(bundles), target)
    lines = [ln for ln in Path(events).read_text().splitlines() if ln.strip()]
    report = ingestor.ingest_batch(lines)
    typer.echo(json.dumps(report.model_dump(exclude={"outcomes"})))


@app.command(name="version")
def version() -> None:
    """Print the package version."""
    from importlib.metadata import version as pkg_version  # noqa: PLC0415

    typer.echo(pkg_version("trax-io-agent-spine"))


if __name__ == "__main__":  # pragma: no cover
    app()
