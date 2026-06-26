"""IAM helpers for tenant-scoped resources.

This module centralizes two things every per-tenant CDK construct in Trax IO
must apply:

* The IAM condition key pattern ``aws:ResourceTag/TenantId == <tenant_id>``
  (SOC 2 onboarding hook #6). Sub-projects building tenant-scoped roles
  should call :func:`tenant_tag_condition` rather than hand-rolling the dict.
* The mandatory tag set (``Project``, ``Compliance``, ``TenantId``) applied
  via :func:`apply_tenant_tags`.
"""
from __future__ import annotations

from typing import Any

from aws_cdk import Tags
from constructs import IConstruct


def tenant_tag_condition(tenant_id: str) -> dict[str, dict[str, str]]:
    """Return the IAM ``Condition`` block scoping a policy to one tenant.

    The returned shape is the JSON condition block used inside
    ``iam.PolicyStatement`` — ``StringEquals`` on the ``aws:ResourceTag/TenantId``
    request context key. Future sub-projects that build tenant-scoped roles
    import this so the condition key is identical everywhere.
    """
    if not tenant_id:
        raise ValueError("tenant_id must be a non-empty string.")
    return {"StringEquals": {"aws:ResourceTag/TenantId": tenant_id}}


def apply_tenant_tags(
    construct: IConstruct,
    *,
    tenant_id: str,
    extra: dict[str, str] | None = None,
) -> None:
    """Apply the standard per-tenant tag set to a CDK construct.

    Standard tags:

    * ``TenantId`` = the tenant's slug.
    * ``Project`` = ``TraxIO``.
    * ``Compliance`` = ``SOC2-TypeII``.

    Additional tags may be supplied via ``extra``. All tags are applied via
    ``Tags.of(construct).add(...)`` so they propagate to every taggable child
    resource beneath ``construct``.
    """
    if not tenant_id:
        raise ValueError("tenant_id must be a non-empty string.")
    tags: Any = Tags.of(construct)
    tags.add("TenantId", tenant_id)
    tags.add("Project", "TraxIO")
    tags.add("Compliance", "SOC2-TypeII")
    if extra:
        for k, v in extra.items():
            tags.add(k, v)
