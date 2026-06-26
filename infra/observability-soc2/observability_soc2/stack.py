"""Trax IO Observability + SOC 2 Control Plane — Phase 2 CDK stack.

Phase 2 makes the stack multi-tenant-aware and exports per-tenant resource
ARNs so downstream stacks (sub-project #1 nightly-extract, #2 feature-store)
can ``Fn::ImportValue`` them.

Singleton (account-scoped) resources — one per account:
  * CloudTrail Lake event data store (7-year retention).
  * AWS Audit Manager SOC 2 assessment.
  * Audit S3 bucket (Object Lock Compliance, 7 years).
  * Audit KMS CMK.
  * OpenTelemetry collector Fargate task.

Per-tenant resources — one per tenant:
  * KMS customer-managed key (annual rotation) — exported as
    ``TraxIo-<tenant_id>-TenantKmsArn``.
  * CloudWatch log group (retained) — exported as
    ``TraxIo-<tenant_id>-TenantLogGroupArn``.

Hard constraints honored:
  * Object Lock is **Compliance** mode (non-bypassable), not Governance.
  * All KMS keys have ``enable_key_rotation=True``.
  * CloudTrail Lake retention = 2557 days (7 years).
  * The CFN export name pattern is a contract — downstream stacks depend on
    the literal strings.
"""
from __future__ import annotations

from collections.abc import Sequence

from aws_cdk import (
    CfnOutput,
    CfnTag,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_auditmanager as auditmanager,
)
from aws_cdk import (
    aws_cloudtrail as cloudtrail,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_kms as kms,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct

from observability_soc2.iam_helpers import apply_tenant_tags
from observability_soc2.tenants import TenantSpec

# SOC 2 evidence retention is 7 years (2557 days). Load-bearing — do NOT
# change without SecOps sign-off.
SOC2_RETENTION_DAYS = 2557

# AWS Audit Manager ships a built-in SOC 2 framework. The ID below is a
# placeholder referenced in the L1 assessment; the real UUID is discovered at
# deploy time via ``aws auditmanager list-assessment-frameworks``.
SOC2_FRAMEWORK_ID_CONTEXT_KEY = "trax_io:soc2_framework_id"
DEFAULT_SOC2_FRAMEWORK_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"


def _construct_id_suffix(tenant_id: str) -> str:
    """Turn a tenant slug into an alphanumeric CDK construct-ID suffix.

    CDK construct IDs must be alphanumeric; tenant slugs are kebab-case. We
    strip hyphens and title-case each segment so the result is deterministic
    and collision-free across tenants (``lighthouse-alpha`` → ``LighthouseAlpha``).
    """
    return "".join(part.capitalize() for part in tenant_id.split("-") if part)


class ObservabilitySoc2Stack(Stack):
    """Phase 2 control-plane stack. Synth only; no deploy path wired."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        tenants: Sequence[TenantSpec],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not tenants:
            raise ValueError(
                "ObservabilitySoc2Stack requires at least one tenant; "
                "got an empty sequence.",
            )
        self.tenants: tuple[TenantSpec, ...] = tuple(tenants)

        # -- Singleton (account-scoped) audit KMS key -------------------------
        # Org-wide rather than per-tenant — every tenant's events land in the
        # same immutable audit store, but envelope keys on the in-flight data
        # path are per-tenant (below).
        self.audit_key = kms.Key(
            self,
            "TraxIoAuditKey",
            alias="alias/trax-io/audit",
            description="CMK for Trax IO audit bucket + CloudTrail Lake (org-wide).",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # -- Singleton: Immutable audit bucket (Object Lock Compliance) -------
        # NOTE: In production this bucket MUST live in a dedicated "Trax
        # audit" AWS account (dual-book pattern). Phase 1/2 keep it in-stack
        # for synth simplicity; a later phase moves it cross-account.
        self.audit_bucket = s3.CfnBucket(
            self,
            "TraxIoAuditBucket",
            bucket_name=None,
            object_lock_enabled=True,
            object_lock_configuration=s3.CfnBucket.ObjectLockConfigurationProperty(
                object_lock_enabled="Enabled",
                rule=s3.CfnBucket.ObjectLockRuleProperty(
                    default_retention=s3.CfnBucket.DefaultRetentionProperty(
                        mode="COMPLIANCE",  # Non-bypassable. Required.
                        days=SOC2_RETENTION_DAYS,
                    ),
                ),
            ),
            versioning_configuration=s3.CfnBucket.VersioningConfigurationProperty(
                status="Enabled",
            ),
            bucket_encryption=s3.CfnBucket.BucketEncryptionProperty(
                server_side_encryption_configuration=[
                    s3.CfnBucket.ServerSideEncryptionRuleProperty(
                        server_side_encryption_by_default=(
                            s3.CfnBucket.ServerSideEncryptionByDefaultProperty(
                                sse_algorithm="aws:kms",
                                kms_master_key_id=self.audit_key.key_arn,
                            )
                        ),
                        bucket_key_enabled=True,
                    )
                ],
            ),
            public_access_block_configuration=(
                s3.CfnBucket.PublicAccessBlockConfigurationProperty(
                    block_public_acls=True,
                    block_public_policy=True,
                    ignore_public_acls=True,
                    restrict_public_buckets=True,
                )
            ),
        )
        self.audit_bucket.add_metadata(
            "trax_io:account_split",
            "PROD: move to dedicated Trax audit account; Phase 1/2 synth keeps in-stack.",
        )
        self.audit_bucket.apply_removal_policy(RemovalPolicy.RETAIN)

        # -- Singleton: CloudTrail Lake event data store (7-year immutable) ---
        self.event_data_store = cloudtrail.CfnEventDataStore(
            self,
            "TraxIoCloudTrailLakeEventStore",
            name="trax-io-soc2-event-store",
            multi_region_enabled=True,
            organization_enabled=False,
            retention_period=SOC2_RETENTION_DAYS,
            termination_protection_enabled=True,
            kms_key_id=self.audit_key.key_arn,
            tags=[
                CfnTag(key="Project", value="TraxIO"),
                CfnTag(key="Owner", value="Platform+SecOps"),
                CfnTag(key="Compliance", value="SOC2-TypeII"),
            ],
        )

        # -- Singleton: Audit Manager SOC 2 framework assessment --------------
        soc2_framework_id = (
            self.node.try_get_context(SOC2_FRAMEWORK_ID_CONTEXT_KEY)
            or DEFAULT_SOC2_FRAMEWORK_PLACEHOLDER
        )
        self.audit_manager_assessment = auditmanager.CfnAssessment(
            self,
            "TraxIoSoc2Assessment",
            name="trax-io-soc2-type-ii",
            description=(
                "SOC 2 Type II continuous assessment for Trax IO. "
                "Evidence accrues from Wave 0 day one; attestation target month 6."
            ),
            framework_id=soc2_framework_id,
            status="ACTIVE",
            assessment_reports_destination=(
                auditmanager.CfnAssessment.AssessmentReportsDestinationProperty(
                    destination=f"s3://{self.audit_bucket.ref}/audit-manager-reports/",
                    destination_type="S3",
                )
            ),
            scope=auditmanager.CfnAssessment.ScopeProperty(
                aws_accounts=[
                    auditmanager.CfnAssessment.AWSAccountProperty(
                        id=self.account,
                        name="TraxAi",
                    )
                ],
                aws_services=[
                    auditmanager.CfnAssessment.AWSServiceProperty(service_name=s)
                    for s in ["s3", "kms", "cloudtrail", "iam", "cloudwatch", "logs"]
                ],
            ),
            roles=[
                auditmanager.CfnAssessment.RoleProperty(
                    role_type="PROCESS_OWNER",
                    role_arn=f"arn:aws:iam::{self.account}:role/TraxIoSecOps",
                )
            ],
        )

        # -- Singleton: OpenTelemetry Collector (Fargate placeholder) ---------
        self.otel_vpc = ec2.Vpc(self, "TraxIoOtelVpc", max_azs=2, nat_gateways=1)
        self.otel_cluster = ecs.Cluster(
            self,
            "TraxIoOtelCluster",
            vpc=self.otel_vpc,
            container_insights=True,
        )
        self.otel_task_role = iam.Role(
            self,
            "TraxIoOtelTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="OTel collector task role — X-Ray + CloudWatch + OpenSearch writer.",
        )
        self.otel_task_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AWSXRayDaemonWriteAccess"),
        )
        self.otel_task_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchAgentServerPolicy"),
        )
        self.otel_task = ecs.FargateTaskDefinition(
            self,
            "TraxIoOtelTaskDef",
            cpu=512,
            memory_limit_mib=1024,
            task_role=self.otel_task_role,
        )
        self.otel_task.add_container(
            "collector",
            image=ecs.ContainerImage.from_registry(
                "public.ecr.aws/aws-observability/aws-otel-collector:latest",
            ),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="otel-collector"),
        )

        # -- Per-tenant resources --------------------------------------------
        self.tenant_keys: dict[str, kms.Key] = {}
        self.tenant_log_groups: dict[str, logs.LogGroup] = {}
        self.tenant_key_arns: dict[str, str] = {}
        self.tenant_log_group_arns: dict[str, str] = {}

        for tenant in self.tenants:
            self._provision_tenant(tenant)

    # ------------------------------------------------------------------ #
    # Per-tenant provisioning                                             #
    # ------------------------------------------------------------------ #
    def _provision_tenant(self, tenant: TenantSpec) -> None:
        suffix = _construct_id_suffix(tenant.tenant_id)
        tenant_id = tenant.tenant_id

        # Per-tenant KMS CMK (annual rotation).
        key = kms.Key(
            self,
            f"TenantKms-{suffix}",
            alias=f"alias/trax-io/tenant/{tenant_id}",
            description=f"Per-tenant CMK for Trax IO tenant '{tenant_id}'.",
            enable_key_rotation=True,  # annual rotation — hard constraint
            rotation_period=Duration.days(365),
            removal_policy=RemovalPolicy.RETAIN,
        )
        apply_tenant_tags(key, tenant_id=tenant_id)
        self.tenant_keys[tenant_id] = key
        self.tenant_key_arns[tenant_id] = key.key_arn

        # Per-tenant CloudWatch log group, encrypted with the tenant's CMK.
        log_group = logs.LogGroup(
            self,
            f"TenantLogGroup-{suffix}",
            log_group_name=f"/trax-io/{tenant_id}",
            retention=logs.RetentionDays.ONE_YEAR,
            encryption_key=key,
            removal_policy=RemovalPolicy.RETAIN,
        )
        apply_tenant_tags(log_group, tenant_id=tenant_id)
        self.tenant_log_groups[tenant_id] = log_group
        self.tenant_log_group_arns[tenant_id] = log_group.log_group_arn

        # -- CFN exports — the contract consumed by downstream stacks -------
        # External stacks Fn::ImportValue these literal names. Changing them
        # is a breaking change.
        CfnOutput(
            self,
            f"TenantKmsArnOutput-{suffix}",
            value=key.key_arn,
            export_name=f"TraxIo-{tenant_id}-TenantKmsArn",
            description=(
                f"KMS CMK ARN for tenant '{tenant_id}'. Consumed by Trax IO "
                "sub-projects (e.g., feature-store) via Fn::ImportValue."
            ),
        )
        CfnOutput(
            self,
            f"TenantLogGroupArnOutput-{suffix}",
            value=log_group.log_group_arn,
            export_name=f"TraxIo-{tenant_id}-TenantLogGroupArn",
            description=(
                f"CloudWatch log group ARN for tenant '{tenant_id}'. Consumed "
                "by Trax IO sub-projects (e.g., nightly-extract) via Fn::ImportValue."
            ),
        )
