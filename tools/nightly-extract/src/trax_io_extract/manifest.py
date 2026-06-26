"""ExtractManifest pydantic models.

Schema v1.0.0 — the handshake between the Nightly Extract Utility
(sub-project #1) and the Feature Store / Glue ingest (sub-project #2).

Contract:
    docs/contracts/2026-04-17-extract-manifest-contract.md
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

# The four date-windowed domains (positions 1–4 in the contract).
# A run where ALL of these failed is flagged "degraded" per the
# contract's atomicity section.
DATE_WINDOWED_DOMAINS: frozenset[str] = frozenset(
    {
        "causal_values",
        "demand_history_rotables",
        "demand_history_expendables",
        "events",
    }
)


class DomainArtifact(BaseModel):
    """One raw landing artifact for one of the 21 domains."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str
    status: Literal["succeeded", "failed", "skipped"]
    s3_uri: str | None
    row_count: NonNegativeInt = 0
    sha256: str | None = None
    bytes: NonNegativeInt = 0
    bind_vars: dict[str, str] = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime
    error_code: str | None = None
    error_message: str | None = None


class ExtractManifest(BaseModel):
    """Emitted once per nightly extract run; landed as ``manifest.json``
    next to the 21 domain artifacts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0.0"] = "1.0.0"
    tenant_id: str
    extract_date: date
    run_id: str
    run_status: Literal["succeeded", "partial", "degraded", "failed"]
    started_at: datetime
    finished_at: datetime
    source: Literal["eMRO-Oracle"] = "eMRO-Oracle"
    source_sql_sha256: str
    extract_utility_version: str
    artifacts: list[DomainArtifact]

    @classmethod
    def from_artifacts(
        cls,
        *,
        tenant_id: str,
        extract_date: date,
        run_id: str,
        started_at: datetime,
        finished_at: datetime,
        source_sql_sha256: str,
        extract_utility_version: str,
        artifacts: list[DomainArtifact],
    ) -> ExtractManifest:
        """Construct a manifest, deriving ``run_status`` from the artifacts
        per the contract's atomicity rules:

        * all succeeded → ``succeeded``
        * at least one failed but at least one succeeded → ``partial``
        * all date-windowed domains (1–4) failed → ``degraded``
          (regardless of how many snapshot domains succeeded)
        * zero succeeded → ``failed``
        """
        succeeded = [a for a in artifacts if a.status == "succeeded"]
        failed = [a for a in artifacts if a.status == "failed"]

        windowed_artifacts = [a for a in artifacts if a.domain in DATE_WINDOWED_DOMAINS]
        windowed_all_failed = bool(windowed_artifacts) and all(
            a.status == "failed" for a in windowed_artifacts
        )

        run_status: Literal["succeeded", "partial", "degraded", "failed"]
        if not succeeded:
            run_status = "failed"
        elif windowed_all_failed:
            # Contract §Atomicity: if ALL date-windowed domains failed,
            # flag "degraded" even if snapshot domains succeeded, so #2
            # holds ingestion pending operator review.
            run_status = "degraded"
        elif failed:
            run_status = "partial"
        else:
            run_status = "succeeded"

        return cls(
            tenant_id=tenant_id,
            extract_date=extract_date,
            run_id=run_id,
            run_status=run_status,
            started_at=started_at,
            finished_at=finished_at,
            source_sql_sha256=source_sql_sha256,
            extract_utility_version=extract_utility_version,
            artifacts=artifacts,
        )
