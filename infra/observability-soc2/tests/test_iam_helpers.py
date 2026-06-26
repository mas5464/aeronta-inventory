"""Tests for the IAM + tagging helpers."""
from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk import assertions
from aws_cdk import aws_kms as kms

from observability_soc2.iam_helpers import apply_tenant_tags, tenant_tag_condition

# ---------------------------------------------------------------------- #
# tenant_tag_condition                                                    #
# ---------------------------------------------------------------------- #


def test_tenant_tag_condition_shape() -> None:
    condition = tenant_tag_condition("lighthouse-alpha")
    assert condition == {
        "StringEquals": {"aws:ResourceTag/TenantId": "lighthouse-alpha"},
    }


def test_tenant_tag_condition_rejects_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        tenant_tag_condition("")


# ---------------------------------------------------------------------- #
# apply_tenant_tags                                                       #
# ---------------------------------------------------------------------- #


class _StubStack(cdk.Stack):
    def __init__(self, scope: cdk.App, tenant_id: str) -> None:
        super().__init__(scope, "StubStack")
        key = kms.Key(self, "Key", enable_key_rotation=True)
        apply_tenant_tags(key, tenant_id=tenant_id)


class _StubStackWithExtra(cdk.Stack):
    def __init__(self, scope: cdk.App, tenant_id: str) -> None:
        super().__init__(scope, "StubStack")
        key = kms.Key(self, "Key", enable_key_rotation=True)
        apply_tenant_tags(
            key,
            tenant_id=tenant_id,
            extra={"SubPlan": "09", "Owner": "Platform+SecOps"},
        )


def _key_tags(stack: cdk.Stack) -> dict[str, str]:
    template = assertions.Template.from_stack(stack)
    keys = template.find_resources("AWS::KMS::Key")
    assert len(keys) == 1
    (props,) = keys.values()
    return {t["Key"]: t["Value"] for t in props["Properties"].get("Tags", [])}


def test_apply_tenant_tags_applies_standard_tag_set() -> None:
    app = cdk.App()
    stack = _StubStack(app, "lighthouse-alpha")
    tags = _key_tags(stack)
    assert tags["TenantId"] == "lighthouse-alpha"
    assert tags["Project"] == "TraxIO"
    assert tags["Compliance"] == "SOC2-TypeII"


def test_apply_tenant_tags_merges_extras() -> None:
    app = cdk.App()
    stack = _StubStackWithExtra(app, "lighthouse-alpha")
    tags = _key_tags(stack)
    assert tags["TenantId"] == "lighthouse-alpha"
    assert tags["Project"] == "TraxIO"
    assert tags["Compliance"] == "SOC2-TypeII"
    assert tags["SubPlan"] == "09"
    assert tags["Owner"] == "Platform+SecOps"


def test_apply_tenant_tags_rejects_empty_tenant_id() -> None:
    app = cdk.App()
    stack = cdk.Stack(app, "EmptyStack")
    key = kms.Key(stack, "Key", enable_key_rotation=True)
    with pytest.raises(ValueError, match="non-empty"):
        apply_tenant_tags(key, tenant_id="")
