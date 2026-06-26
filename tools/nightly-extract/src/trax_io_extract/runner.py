"""Extract-runner: executes the 21 domains and emits a manifest.

Kept Click-free so tests can drive it with a fake connection factory.
Per-domain isolation: one domain failing never aborts the others.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from trax_io_extract import __version__
from trax_io_extract.domains import DOMAINS, Domain
from trax_io_extract.manifest import DomainArtifact, ExtractManifest
from trax_io_extract.oracle import OracleExecutionError, execute_domain


ConnFactory = Callable[[], AbstractContextManager[Any]]
BindResolver = Callable[[Domain], dict[str, Any]]


@dataclass(frozen=True)
class DomainRunResult:
    """Outcome of running a single domain."""

    domain: str
    status: str  # "succeeded" | "failed"
    row_count: int
    sha256: str | None
    bytes: int
    bind_vars: dict[str, str]
    started_at: datetime
    finished_at: datetime
    error_code: str | None
    error_message: str | None
    rows: list[dict[str, Any]] | None


def _serialize_binds(binds: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in binds.items():
        if isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        else:
            out[k] = str(v)
    return out


def _compute_source_sql_sha256(sql_dir: Path) -> str:
    """Hash the 21 SQL files in canonical domain order."""
    h = hashlib.sha256()
    for domain in DOMAINS:
        path = sql_dir / domain.sql_file
        h.update(domain.sql_file.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def run_domain(
    *,
    domain: Domain,
    sql_dir: Path,
    output_dir: Path,
    binds: dict[str, Any],
    conn_factory: ConnFactory,
) -> DomainRunResult:
    """Execute one domain end-to-end. Catches Oracle errors."""
    started_at = datetime.now(timezone.utc)
    serialized_binds = _serialize_binds(binds)

    sql_path = sql_dir / domain.sql_file
    sql_text = sql_path.read_text(encoding="utf-8")

    try:
        with conn_factory() as conn:
            rows, row_count = execute_domain(
                conn=conn, sql_text=sql_text, binds=binds
            )
    except OracleExecutionError as exc:
        finished_at = datetime.now(timezone.utc)
        return DomainRunResult(
            domain=domain.name,
            status="failed",
            row_count=0,
            sha256=None,
            bytes=0,
            bind_vars=serialized_binds,
            started_at=started_at,
            finished_at=finished_at,
            error_code=exc.error_code,
            error_message=exc.message,
            rows=None,
        )

    # Serialize to <domain>.json with sorted keys, UTF-8.
    out_path = output_dir / f"{domain.name}.json"
    payload = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    out_path.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()

    finished_at = datetime.now(timezone.utc)
    return DomainRunResult(
        domain=domain.name,
        status="succeeded",
        row_count=row_count,
        sha256=sha,
        bytes=len(payload),
        bind_vars=serialized_binds,
        started_at=started_at,
        finished_at=finished_at,
        error_code=None,
        error_message=None,
        rows=rows,
    )


def _result_to_artifact(result: DomainRunResult) -> DomainArtifact:
    return DomainArtifact(
        domain=result.domain,
        status="succeeded" if result.status == "succeeded" else "failed",
        s3_uri=None,
        row_count=result.row_count,
        sha256=result.sha256,
        bytes=result.bytes,
        bind_vars=result.bind_vars,
        started_at=result.started_at,
        finished_at=result.finished_at,
        error_code=result.error_code,
        error_message=result.error_message,
    )


def run_extract(
    *,
    domains_to_run: Sequence[Domain],
    sql_dir: Path,
    output_dir: Path,
    bind_resolver: BindResolver,
    conn_factory: ConnFactory,
    tenant_id: str,
    extract_date: date,
    run_id: str,
) -> ExtractManifest:
    """Run each domain sequentially, emit manifest.json, return the manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc)
    source_sql_sha256 = _compute_source_sql_sha256(sql_dir)

    artifacts: list[DomainArtifact] = []
    for domain in domains_to_run:
        binds = bind_resolver(domain)
        result = run_domain(
            domain=domain,
            sql_dir=sql_dir,
            output_dir=output_dir,
            binds=binds,
            conn_factory=conn_factory,
        )
        artifacts.append(_result_to_artifact(result))

    finished_at = datetime.now(timezone.utc)
    manifest = ExtractManifest.from_artifacts(
        tenant_id=tenant_id,
        extract_date=extract_date,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        source_sql_sha256=source_sql_sha256,
        extract_utility_version=__version__,
        artifacts=artifacts,
    )
    (output_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    return manifest
