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
    writeback_url: str = typer.Option("http://localhost:9000", "--writeback-url"),
) -> None:
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir, tenant_id=tenant)
    stamp = datetime.fromisoformat(now) if now else datetime.now(UTC)
    target = RestWritebackClient(writeback_url) if apply else InMemoryWritebackTarget()
    supervisor = Supervisor(feature_store=fs, inventory_state=inv, writeback=target)
    result = supervisor.run(tenant=TenantContext(tenant_id=tenant_id), keys=keys, now=stamp)
    typer.echo(json.dumps(result.summary))


@app.command(name="version")
def version() -> None:
    """Print the package version."""
    from importlib.metadata import version as pkg_version  # noqa: PLC0415

    typer.echo(pkg_version("trax-io-agent-spine"))


if __name__ == "__main__":  # pragma: no cover
    app()
