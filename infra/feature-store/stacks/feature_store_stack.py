"""Per-tenant Feature Store CDK stack (synth only).

Wires together:
  * KMS customer-managed key (envelope encryption, tenant-scoped).
  * S3 landing bucket (nightly extract drop zone, SSE-KMS).
  * S3 lake bucket backing Iceberg tables, partitioned by
    tenant_id / extract_date.
  * AWS Glue database + Iceberg tables for the 10 v1 feature groups
    (design §4.2). Partitioning is (tenant_id, extract_date); format-version
    is 2 to enable time-travel.
  * DynamoDB online-features table keyed on (tenant_id, pn, location) per
    design §4.2.

Phase 1 scaffold deliberately does NOT provision Glue jobs, Kinesis
streams, EventBridge rules, or cross-region replication — those arrive in
Phases 2, 5, and 7. Tables and IAM-ready surfaces are enough to unblock
the downstream Agent Spine work against synthesized ARNs.
"""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_dynamodb as dynamodb,
    aws_glue as glue,
    aws_iam as iam,
    aws_kms as kms,
    aws_s3 as s3,
    aws_s3_assets as s3_assets,
)
from constructs import Construct

from stacks.iceberg_schemas import FEATURE_GROUP_SCHEMAS

# Path to the Phase 2 PySpark job source. Resolved relative to this file so
# `cdk synth` works regardless of cwd. The service package lives in a sibling
# top-level folder (`services/feature-store`).
# parents[0]=stacks, parents[1]=feature-store, parents[2]=infra, parents[3]=repo root.
_GLUE_SRC_DIR = (
    Path(__file__).resolve().parents[3]
    / "services"
    / "feature-store"
    / "src"
    / "trax_io_feature_store"
    / "glue"
)

# Feature groups that ship a PySpark materialization Glue job today.
_GLUE_FEATURE_GROUPS = (
    "demand_history",
    "stock_position",
    "current_policy",
    "vendor_economics",
    "part_attributes",
    "criticality",
    "lead_time_distribution",
    "open_orders_snapshot",
    "interchangeable_graph",
    "location_graph",
)


class FeatureStoreStack(cdk.Stack):
    """One stack per tenant. Multi-tenant deploy is a `for tenant in tenants`."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        tenant_id: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)  # type: ignore[arg-type]

        self.tenant_id = tenant_id
        cdk.Tags.of(self).add("TenantId", tenant_id)
        cdk.Tags.of(self).add("Project", "TraxIO")

        # -------- KMS --------
        # Per-tenant CMK. Annual rotation per design §4.5.
        self.cmk = kms.Key(
            self,
            "TenantCmk",
            alias=f"trax-io/{tenant_id}",
            description=f"Trax IO feature-store envelope-encryption CMK for tenant={tenant_id}",
            enable_key_rotation=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # -------- S3 landing bucket --------
        # Nightly extracts from sub-project #1 land here under
        # s3://<bucket>/<tenant_id>/<extract_date>/manifest.json
        self.landing_bucket = s3.Bucket(
            self,
            "LandingBucket",
            bucket_name=None,  # let CDK auto-name; deterministic name comes in Phase 8
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.cmk,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # -------- S3 lake bucket --------
        # Backs Iceberg tables. Object Lock on the audit subset arrives in
        # Phase 7 task 30 via a sibling audit bucket.
        self.lake_bucket = s3.Bucket(
            self,
            "LakeBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.cmk,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # -------- Glue database + Iceberg tables --------
        self.glue_database = glue.CfnDatabase(
            self,
            "LakeDatabase",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=f"trax_io_lake_{tenant_id}".replace("-", "_"),
                description=(
                    f"Trax IO data-lake Glue database for tenant={tenant_id}. "
                    "Iceberg tables, partitioned by (tenant_id, extract_date)."
                ),
            ),
        )

        for feature_group, columns in FEATURE_GROUP_SCHEMAS.items():
            self._make_iceberg_table(feature_group=feature_group, columns=columns)

        # -------- DynamoDB online-features table --------
        # PK = tenant_id, SK = pn#location. Production keyset matches design
        # §4.2 wording (tenant_id, pn, location); encoding location+pn into
        # the SK keeps point lookups at O(1) and permits range scans by pn.
        self.online_table = dynamodb.Table(
            self,
            "OnlineFeatures",
            partition_key=dynamodb.Attribute(
                name="tenant_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="pn_location", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=self.cmk,
            point_in_time_recovery=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # -------- Glue ETL jobs: one per materialized feature group (Phase 2) --------
        # Each mirrors `services/feature-store/.../glue/<group>_job.py`.
        self.glue_jobs = {
            fg: self._make_glue_job(feature_group=fg) for fg in _GLUE_FEATURE_GROUPS
        }
        self.demand_history_job = self.glue_jobs["demand_history"]

        # -------- Outputs (consumed by Phase 2 Glue jobs + the Agent Spine) --------
        cdk.CfnOutput(self, "LandingBucketName", value=self.landing_bucket.bucket_name)
        cdk.CfnOutput(self, "LakeBucketName", value=self.lake_bucket.bucket_name)
        cdk.CfnOutput(self, "OnlineTableName", value=self.online_table.table_name)
        cdk.CfnOutput(self, "CmkArn", value=self.cmk.key_arn)
        cdk.CfnOutput(self, "GlueDatabaseName", value=self.glue_database.ref)

    # ------------------------------------------------------------------

    def _make_iceberg_table(
        self, *, feature_group: str, columns: list[tuple[str, str]]
    ) -> glue.CfnTable:
        """Synthesize one Iceberg table for a feature group.

        Partitioning is (tenant_id, extract_date) per user instructions.
        Format version 2 enables Iceberg time-travel, which is non-negotiable
        for SOC 2 reproducibility (design §4.2).
        """
        table_name = f"raw_{feature_group}"
        return glue.CfnTable(
            self,
            f"Table{feature_group.title().replace('_', '')}",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_name=self.glue_database.ref,
            table_input=glue.CfnTable.TableInputProperty(
                name=table_name,
                description=(
                    f"Iceberg table for feature group '{feature_group}' "
                    f"(tenant={self.tenant_id}). Partitioned by (tenant_id, extract_date)."
                ),
                table_type="EXTERNAL_TABLE",
                parameters={
                    "classification": "iceberg",
                    "table_type": "ICEBERG",
                    "format-version": "2",
                    "project": "TraxIO",
                },
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    columns=[
                        glue.CfnTable.ColumnProperty(name=n, type=t) for n, t in columns
                    ],
                    location=(
                        f"s3://{self.lake_bucket.bucket_name}/"
                        f"{feature_group}/tenant_id={self.tenant_id}/"
                    ),
                ),
                partition_keys=[
                    glue.CfnTable.ColumnProperty(name="tenant_id", type="string"),
                    glue.CfnTable.ColumnProperty(name="extract_date", type="date"),
                ],
            ),
        )

    # ------------------------------------------------------------------

    def _make_glue_job(self, *, feature_group: str) -> glue.CfnJob:
        """Package and deploy one feature group's PySpark Glue job.

        Creates an `aws_s3_assets.Asset` (the script), a least-privilege IAM role
        (glue.amazonaws.com) scoped to this tenant's KMS key + landing/lake buckets,
        and an `AWS::Glue::Job` (glueetl, glue_version=4.0) pointing at the asset.
        """
        script_src = _GLUE_SRC_DIR / f"{feature_group}_job.py"
        if not script_src.exists():
            raise FileNotFoundError(
                "PySpark job source not found at expected path "
                f"{script_src!s}. The CDK stack expects the services/feature-store "
                "package to be a sibling of infra/feature-store."
            )
        pascal = "".join(w.capitalize() for w in feature_group.split("_"))  # stock_position->StockPosition
        job_slug = feature_group.replace("_", "-")

        # S3 asset for the PySpark script. CDK uploads to its assets bucket
        # and the Glue job pulls from the asset's S3 URI at run time.
        script_asset = s3_assets.Asset(
            self,
            f"{pascal}JobScript",
            path=str(script_src),
        )

        # Least-privilege role. We do NOT rely on AWSGlueServiceRole alone --
        # we also grant explicit resource-scoped S3 + KMS statements so the
        # blast radius of an over-broad managed policy is bounded.
        role = iam.Role(
            self,
            f"{pascal}JobRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            description=(
                f"Role for the {feature_group} Glue ETL job (tenant={self.tenant_id})."
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSGlueServiceRole"
                ),
            ],
        )
        cdk.Tags.of(role).add("Project", "TraxIO")
        cdk.Tags.of(role).add("TenantId", self.tenant_id)

        # Tenant KMS key: tight grants for envelope encryption of S3 reads/writes.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "kms:Decrypt",
                    "kms:GenerateDataKey",
                    "kms:DescribeKey",
                ],
                resources=[self.cmk.key_arn],
            )
        )

        # Landing bucket: read-only (+list). This is the raw-JSON drop zone.
        self.landing_bucket.grant_read(role)

        # Lake bucket: read+write. Iceberg needs read for metadata merges.
        self.lake_bucket.grant_read_write(role)

        # Glue catalog access for the target Iceberg table. Resource-scoped to
        # this tenant's database to prevent cross-tenant catalog reads.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:UpdateTable",
                    "glue:GetPartitions",
                    "glue:BatchCreatePartition",
                    "glue:BatchUpdatePartition",
                ],
                resources=[
                    f"arn:aws:glue:{self.region}:{self.account}:catalog",
                    f"arn:aws:glue:{self.region}:{self.account}:database/"
                    f"{self.glue_database.ref}",
                    f"arn:aws:glue:{self.region}:{self.account}:table/"
                    f"{self.glue_database.ref}/*",
                ],
            )
        )

        # Read the script asset from the CDK assets bucket.
        script_asset.grant_read(role)

        # Assemble the Glue job. G.1X / 2 workers is a Phase 2 template;
        # production sizing lands with the full 10-job rollout.
        job = glue.CfnJob(
            self,
            f"{pascal}Job",
            name=f"{self.tenant_id}-{job_slug}-job",
            role=role.role_arn,
            glue_version="4.0",
            worker_type="G.1X",
            number_of_workers=2,
            command=glue.CfnJob.JobCommandProperty(
                name="glueetl",
                python_version="3",
                script_location=script_asset.s3_object_url,
            ),
            default_arguments={
                "--job-language": "python",
                "--enable-metrics": "",
                "--enable-continuous-cloudwatch-log": "true",
                "--TempDir": f"s3://{self.lake_bucket.bucket_name}/_glue-tmp/",
            },
            execution_property=glue.CfnJob.ExecutionPropertyProperty(
                max_concurrent_runs=1,
            ),
            tags={"Project": "TraxIO", "TenantId": self.tenant_id},
        )
        return job
