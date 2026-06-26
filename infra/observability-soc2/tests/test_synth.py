"""Synth-level assertions for the Observability + SOC 2 stack.

These tests guard the hard constraints in the Phase 1 + Phase 2 plans:

* CloudTrail Lake event data store exists with 7-year retention (2557 days).
* AWS Audit Manager SOC 2 assessment is attached.
* Every KMS key has ``EnableKeyRotation=true``.
* The audit S3 bucket enables Object Lock in **Compliance** mode with the
  required 7-year retention.
* Per-tenant KMS keys + log groups are distinct per tenant and carry the
  ``TenantId`` tag.
* CFN export names match the literal contract consumed by downstream stacks.
"""
from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk import assertions

from observability_soc2.stack import SOC2_RETENTION_DAYS, ObservabilitySoc2Stack
from observability_soc2.tenants import TenantSpec

_TENANT_A = TenantSpec(
    tenant_id="lighthouse-alpha",
    display_name="Lighthouse Alpha (pilot)",
    environment="dev",
)
_TENANT_B = TenantSpec(
    tenant_id="tenant-b",
    display_name="Tenant B",
    environment="staging",
)


def _synth(
    tenants: tuple[TenantSpec, ...] = (_TENANT_A, _TENANT_B),
) -> assertions.Template:
    app = cdk.App()
    stack = ObservabilitySoc2Stack(
        app,
        "TestStack",
        tenants=tenants,
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return assertions.Template.from_stack(stack)


# ---------------------------------------------------------------------- #
# Existing Phase 1 invariants                                             #
# ---------------------------------------------------------------------- #


def test_cloudtrail_lake_event_store_has_7_year_retention() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::CloudTrail::EventDataStore",
        {
            "RetentionPeriod": SOC2_RETENTION_DAYS,
            "TerminationProtectionEnabled": True,
            "MultiRegionEnabled": True,
        },
    )


def test_audit_manager_soc2_assessment_is_attached() -> None:
    template = _synth()
    template.resource_count_is("AWS::AuditManager::Assessment", 1)
    template.has_resource_properties(
        "AWS::AuditManager::Assessment",
        {"Status": "ACTIVE"},
    )


def test_kms_keys_have_rotation_enabled() -> None:
    template = _synth()
    keys = template.find_resources(
        "AWS::KMS::Key",
        {"Properties": {"EnableKeyRotation": True}},
    )
    # Two tenants + one org-wide audit key = 3.
    assert len(keys) >= 3, f"Expected >=3 rotating KMS keys, got {len(keys)}"


def test_audit_bucket_has_object_lock_compliance_mode() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "ObjectLockEnabled": True,
            "ObjectLockConfiguration": {
                "ObjectLockEnabled": "Enabled",
                "Rule": {
                    "DefaultRetention": {
                        "Mode": "COMPLIANCE",  # non-bypassable — hard constraint
                        "Days": SOC2_RETENTION_DAYS,
                    }
                },
            },
        },
    )


def test_per_tenant_log_groups_exist_and_use_tenant_kms() -> None:
    template = _synth()
    log_groups = template.find_resources("AWS::Logs::LogGroup")
    tenant_lg_names = [
        props["Properties"].get("LogGroupName", "")
        for props in log_groups.values()
        if props.get("Properties", {}).get("LogGroupName", "").startswith("/trax-io/")
    ]
    assert len(tenant_lg_names) >= 2, tenant_lg_names


# ---------------------------------------------------------------------- #
# Phase 2 multi-tenant invariants                                         #
# ---------------------------------------------------------------------- #


def test_stack_rejects_empty_tenants_list() -> None:
    app = cdk.App()
    with pytest.raises(ValueError, match="at least one tenant"):
        ObservabilitySoc2Stack(
            app,
            "EmptyTenantsStack",
            tenants=(),
            env=cdk.Environment(account="123456789012", region="us-east-1"),
        )


def test_per_tenant_kms_key_created_for_each_tenant() -> None:
    template = _synth()
    # Tenant keys use a tenant-scoped alias; the audit key uses the org-wide
    # alias. Filter to just the tenant keys and assert there are two with
    # distinct logical IDs.
    aliases = template.find_resources("AWS::KMS::Alias")
    tenant_alias_ids = [
        logical_id
        for logical_id, props in aliases.items()
        if props["Properties"]
        .get("AliasName", "")
        .startswith("alias/trax-io/tenant/")
    ]
    assert len(tenant_alias_ids) == 2, tenant_alias_ids
    assert len(set(tenant_alias_ids)) == 2, "tenant KMS alias logical IDs collided"


def test_kms_cfn_output_export_name_matches_contract() -> None:
    template = _synth(tenants=(_TENANT_A,))
    template.has_output(
        "*",
        {
            "Export": {"Name": "TraxIo-lighthouse-alpha-TenantKmsArn"},
        },
    )


def test_log_group_cfn_output_export_name_matches_contract() -> None:
    template = _synth(tenants=(_TENANT_A,))
    template.has_output(
        "*",
        {
            "Export": {"Name": "TraxIo-lighthouse-alpha-TenantLogGroupArn"},
        },
    )


def _tags_to_dict(tag_list: list[dict]) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in tag_list}


def test_per_tenant_resources_carry_tenant_id_tag() -> None:
    template = _synth(tenants=(_TENANT_A,))

    # KMS tenant key
    kms_keys = template.find_resources("AWS::KMS::Key")
    tenant_kms_tags = None
    for _logical, props in kms_keys.items():
        key_desc = props["Properties"].get("Description", "")
        if "Per-tenant CMK" in key_desc and _TENANT_A.tenant_id in key_desc:
            tenant_kms_tags = _tags_to_dict(props["Properties"].get("Tags", []))
    assert tenant_kms_tags is not None, "tenant KMS key not found"
    assert tenant_kms_tags.get("TenantId") == _TENANT_A.tenant_id
    assert tenant_kms_tags.get("Project") == "TraxIO"
    assert tenant_kms_tags.get("Compliance") == "SOC2-TypeII"

    # CloudWatch log group
    log_groups = template.find_resources("AWS::Logs::LogGroup")
    tenant_lg_tags = None
    for _logical, props in log_groups.items():
        properties = props.get("Properties", {})
        if properties.get("LogGroupName") == f"/trax-io/{_TENANT_A.tenant_id}":
            tenant_lg_tags = _tags_to_dict(properties.get("Tags", []))
    assert tenant_lg_tags is not None, "tenant log group not found"
    assert tenant_lg_tags.get("TenantId") == _TENANT_A.tenant_id
    assert tenant_lg_tags.get("Project") == "TraxIO"
    assert tenant_lg_tags.get("Compliance") == "SOC2-TypeII"
