"""Drive ExtractManifest.from_artifacts through the four atomicity cases."""

from __future__ import annotations

from datetime import date, datetime, timezone

from trax_io_extract.domains import DOMAINS
from trax_io_extract.manifest import DATE_WINDOWED_DOMAINS, DomainArtifact, ExtractManifest


def _art(domain: str, status: str) -> DomainArtifact:
    now = datetime.now(timezone.utc)
    return DomainArtifact(
        domain=domain,
        status=status,  # type: ignore[arg-type]
        s3_uri=None,
        row_count=0,
        sha256=None,
        bytes=0,
        bind_vars={},
        started_at=now,
        finished_at=now,
        error_code="ORA-00942" if status == "failed" else None,
        error_message="table not found" if status == "failed" else None,
    )


def _build(artifacts: list[DomainArtifact]) -> ExtractManifest:
    now = datetime.now(timezone.utc)
    return ExtractManifest.from_artifacts(
        tenant_id="t",
        extract_date=date(2026, 4, 16),
        run_id="01JS7W2FEXAMPLE0000000000",
        started_at=now,
        finished_at=now,
        source_sql_sha256="0" * 64,
        extract_utility_version="0.1.0",
        artifacts=artifacts,
    )


def test_all_succeeded() -> None:
    arts = [_art(d.name, "succeeded") for d in DOMAINS]
    assert _build(arts).run_status == "succeeded"


def test_one_failed_is_partial() -> None:
    arts = [_art(d.name, "succeeded") for d in DOMAINS]
    # Fail one snapshot domain only — windowed ones still succeed.
    arts[-1] = _art(DOMAINS[-1].name, "failed")
    assert _build(arts).run_status == "partial"


def test_all_windowed_failed_is_degraded() -> None:
    arts: list[DomainArtifact] = []
    for d in DOMAINS:
        status = "failed" if d.name in DATE_WINDOWED_DOMAINS else "succeeded"
        arts.append(_art(d.name, status))
    assert _build(arts).run_status == "degraded"


def test_nothing_succeeded_is_failed() -> None:
    arts = [_art(d.name, "failed") for d in DOMAINS]
    assert _build(arts).run_status == "failed"
