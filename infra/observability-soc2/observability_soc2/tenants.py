"""Tenant registry for the Trax IO Observability + SOC 2 Control Plane.

Phase 2 introduces multi-tenant-aware provisioning. The set of tenants for
which the stack materializes per-tenant resources (KMS CMKs, CloudWatch log
groups) lives here, as a frozen registry. CI can override the registry at
synth time via the ``TRAX_IO_TENANTS_JSON`` environment variable (a JSON list
of TenantSpec dicts) — this is the mechanism by which per-environment or
per-account tenant catalogs are injected without code changes.

Real tenants are added to :data:`LIGHTHOUSE_TENANTS` as they onboard.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Literal

# Slug rule: lowercase alpha first char, then alphanumerics or hyphens, 3–32
# chars total. Underscores are forbidden (CloudTrail / S3 bucket naming).
_TENANT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,31}$")

_ENV_OVERRIDE = "TRAX_IO_TENANTS_JSON"


@dataclass(frozen=True)
class TenantSpec:
    """Declarative spec for a single Trax IO tenant.

    Fields:
      tenant_id:      Slug matching ``^[a-z][a-z0-9-]{2,31}$`` (no
                      underscores, must start with a letter).
      display_name:   Human-readable name (shown in dashboards / tags).
      environment:    One of ``"dev" | "staging" | "prod"``.
      data_residency: AWS region where tenant data lives; defaults to
                      ``us-east-1``.
    """

    tenant_id: str
    display_name: str
    environment: Literal["dev", "staging", "prod"]
    data_residency: Literal["us-east-1", "eu-west-1"] = "us-east-1"

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not _TENANT_ID_PATTERN.match(
            self.tenant_id,
        ):
            raise ValueError(
                f"Invalid tenant_id {self.tenant_id!r}: must match "
                f"{_TENANT_ID_PATTERN.pattern} (lowercase, start with letter, "
                "3–32 chars, no underscores).",
            )
        if self.environment not in ("dev", "staging", "prod"):
            raise ValueError(
                f"Invalid environment {self.environment!r}: "
                "must be 'dev', 'staging', or 'prod'.",
            )
        if self.data_residency not in ("us-east-1", "eu-west-1"):
            raise ValueError(
                f"Invalid data_residency {self.data_residency!r}: "
                "must be 'us-east-1' or 'eu-west-1'.",
            )


# Phase 2 ships with the single pilot tenant. Real tenants are added to this
# tuple as they onboard — it is the in-code source of truth until we move the
# registry to DynamoDB in a later wave.
LIGHTHOUSE_TENANTS: tuple[TenantSpec, ...] = (
    TenantSpec(
        tenant_id="lighthouse-alpha",
        display_name="Lighthouse Alpha (pilot)",
        environment="dev",
    ),
)


def load_tenants_from_env() -> tuple[TenantSpec, ...]:
    """Return the tenant tuple to provision resources for.

    If ``TRAX_IO_TENANTS_JSON`` is set in the environment, parse it as a JSON
    list of TenantSpec dicts and return those; otherwise return
    :data:`LIGHTHOUSE_TENANTS`. This is the CI override seam — production
    synth can inject a different catalog without code changes.
    """
    raw = os.environ.get(_ENV_OVERRIDE)
    if raw is None or raw.strip() == "":
        return LIGHTHOUSE_TENANTS
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError(
            f"{_ENV_OVERRIDE} must decode to a JSON list; "
            f"got {type(parsed).__name__}.",
        )
    return tuple(TenantSpec(**item) for item in parsed)
