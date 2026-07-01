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
from trax_io_extract.landing import LandingSink
from trax_io_extract.manifest import DomainArtifact, ExtractManifest
from trax_io_extract.oracle import OracleExecutionError, execute_domain
from trax_io_extract.scope import ExtractScope, wrap_scoped_sql


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
    uri: str | None = None  # landing URI (s3:// or local path) of the written artifact


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
    sink: LandingSink,
    binds: dict[str, Any],
    conn_factory: ConnFactory,
    scope: ExtractScope | None = None,
) -> DomainRunResult:
    """Execute one domain end-to-end and land its artifact via ``sink``. Catches Oracle errors.

    When ``scope`` is given, the domain's SQL is wrapped per its
    :attr:`Domain.scope_key` (see :func:`trax_io_extract.scope.wrap_scoped_sql`)
    and the scope binds are merged with the domain's own date binds. When
    ``scope`` is ``None`` (the default), this is byte-identical to the
    unscoped extract.
    """
    started_at = datetime.now(timezone.utc)

    sql_path = sql_dir / domain.sql_file
    sql_text = sql_path.read_text(encoding="utf-8")

    scoped_sql, scope_binds = wrap_scoped_sql(sql_text, domain.scope_key, scope)
    merged_binds = {**binds, **scope_binds}
    serialized_binds = _serialize_binds(merged_binds)

    try:
        with conn_factory() as conn:
            rows, row_count = execute_domain(
                conn=conn, sql_text=scoped_sql, binds=merged_binds
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
            uri=None,
        )

    # Serialize to <domain>.json with sorted keys, UTF-8, and land it via the sink.
    payload = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    uri = sink.write(f"{domain.name}.json", payload)
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
        uri=uri,
    )


def _result_to_artifact(result: DomainRunResult) -> DomainArtifact:
    return DomainArtifact(
        domain=result.domain,
        status="succeeded" if result.status == "succeeded" else "failed",
        s3_uri=result.uri,
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
    sink: LandingSink,
    bind_resolver: BindResolver,
    conn_factory: ConnFactory,
    tenant_id: str,
    extract_date: date,
    run_id: str,
    scope: ExtractScope | None = None,
) -> ExtractManifest:
    """Run each domain sequentially, land each artifact + the manifest via ``sink``, return it.

    The manifest is landed LAST so that downstream (#2 Glue) only ever sees a complete,
    integrity-verifiable manifest whose artifact URIs are all populated.

    ``scope``, when given, restricts every ``part``/``part_location``-scopable
    domain to the resolved station + part-cap subset (see
    :mod:`trax_io_extract.scope`). ``None`` (the default) runs unscoped,
    unchanged from prior behavior."""
    started_at = datetime.now(timezone.utc)
    source_sql_sha256 = _compute_source_sql_sha256(sql_dir)

    artifacts: list[DomainArtifact] = []
    for domain in domains_to_run:
        binds = bind_resolver(domain)
        result = run_domain(
            domain=domain,
            sql_dir=sql_dir,
            sink=sink,
            binds=binds,
            conn_factory=conn_factory,
            scope=scope,
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
    # Manifest is the LAST write by design: a sink failure on any domain above propagates
    # (run_domain only catches Oracle errors) and aborts before this, so a crashed run
    # leaves an incomplete, manifest-less prefix that #2 Glue ignores. Do not wrap the loop
    # in a broad except — that would land a manifest over a half-written prefix.
    sink.write("manifest.json", manifest.model_dump_json(indent=2).encode("utf-8"))
    return manifest
