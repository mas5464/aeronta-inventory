"""ExtractManifest round-trip and schema tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

from trax_io_extract.manifest import DomainArtifact, ExtractManifest


def _artifact(domain: str, status: str = "succeeded") -> DomainArtifact:
    now = datetime.now(timezone.utc)
    return DomainArtifact(
        domain=domain,
        status=status,  # type: ignore[arg-type]
        s3_uri=f"s3://bucket/{domain}.json" if status == "succeeded" else None,
        row_count=0,
        sha256="0" * 64 if status == "succeeded" else None,
        bytes=2,
        bind_vars={},
        started_at=now,
        finished_at=now,
    )


def _manifest(artifacts: list[DomainArtifact]) -> ExtractManifest:
    now = datetime.now(timezone.utc)
    return ExtractManifest.from_artifacts(
        tenant_id="lighthouse-01",
        extract_date=date(2026, 4, 16),
        run_id="01JS7W2FEXAMPLE0000000000",
        started_at=now,
        finished_at=now,
        source_sql_sha256="a" * 64,
        extract_utility_version="0.1.0",
        artifacts=artifacts,
    )


def test_round_trip_json() -> None:
    arts = [_artifact(f"d{i}") for i in range(3)]
    m = _manifest(arts)
    dumped = m.model_dump_json()
    restored = ExtractManifest.model_validate_json(dumped)
    assert restored == m
    assert restored.schema_version == "1.0.0"


def test_schema_version_is_1_0_0() -> None:
    m = _manifest([_artifact("d1")])
    assert m.schema_version == "1.0.0"


def test_partial_run_status_with_one_failed() -> None:
    arts = [_artifact(f"snapshot_{i}", "succeeded") for i in range(20)]
    arts.append(_artifact("failed_one", "failed"))
    m = _manifest(arts)
    assert m.run_status == "partial"
