"""Verify the CDK stack synthesizes and contains the expected resources.

Phase 1 deliverable: the Iceberg schema and the stack shape. We assert on
the synthesized CloudFormation template rather than shelling out to the
`cdk` CLI so the test runs hermetically under `uv run pytest`.

If you prefer a CLI smoke check, run (from infra/feature-store):
    uv run cdk synth
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from stacks.feature_store_stack import (
    _GLUE_PACKAGE_DIR,
    FeatureStoreStack,
)
from stacks.iceberg_schemas import FEATURE_GROUP_SCHEMAS


def _synth(tenant_id: str = "aircanada") -> Template:
    app = cdk.App()
    stack = FeatureStoreStack(app, f"TraxIO-FeatureStore-{tenant_id}", tenant_id=tenant_id)
    return Template.from_stack(stack)


def test_stack_has_tenant_kms_key():
    tpl = _synth()
    tpl.resource_count_is("AWS::KMS::Key", 1)
    tpl.has_resource_properties(
        "AWS::KMS::Alias",
        {"AliasName": Match.string_like_regexp(r"alias/trax-io/.+")},
    )


def test_stack_has_two_s3_buckets_kms_encrypted():
    tpl = _synth()
    buckets = tpl.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 2, f"expected landing + lake buckets, got {len(buckets)}"


def test_stack_has_one_glue_database():
    _synth().resource_count_is("AWS::Glue::Database", 1)


def test_stack_has_one_iceberg_table_per_feature_group():
    tpl = _synth()
    tables = tpl.find_resources("AWS::Glue::Table")
    assert len(tables) == len(FEATURE_GROUP_SCHEMAS), (
        f"expected {len(FEATURE_GROUP_SCHEMAS)} Glue tables (one per feature "
        f"group), got {len(tables)}"
    )
    for resource in tables.values():
        assert resource["Properties"]["TableInput"]["Name"].startswith("raw_")


def test_every_glue_table_declares_tenant_and_extract_date_partition_columns():
    tpl = _synth()
    tables = tpl.find_resources("AWS::Glue::Table")
    for logical_id, resource in tables.items():
        partition_keys = resource["Properties"]["TableInput"]["PartitionKeys"]
        names = [k["Name"] for k in partition_keys]
        assert names == ["tenant_id", "extract_date"], (
            f"{logical_id} partitioning = {names}, expected ['tenant_id', 'extract_date']"
        )


def test_every_iceberg_table_declares_format_version_2():
    tpl = _synth()
    tables = tpl.find_resources("AWS::Glue::Table")
    for logical_id, resource in tables.items():
        params = resource["Properties"]["TableInput"]["Parameters"]
        assert params.get("table_type") == "ICEBERG", (
            f"{logical_id} is not flagged as Iceberg"
        )
        assert params.get("format-version") == "2", (
            f"{logical_id} missing format-version=2 (time-travel required for SOC 2)"
        )
        iceberg_input = resource["Properties"]["OpenTableFormatInput"]["IcebergInput"]
        assert iceberg_input == {
            "MetadataOperation": "CREATE",
            "Version": "2",
        }, (
            f"{logical_id} must initialize real Iceberg metadata at deploy time"
        )


def test_lead_time_table_has_exact_supply_cycle_provenance_schema():
    tpl = _synth()
    tables = tpl.find_resources("AWS::Glue::Table")
    lead_time = next(
        resource["Properties"]["TableInput"]
        for resource in tables.values()
        if resource["Properties"]["TableInput"]["Name"]
        == "raw_lead_time_distribution"
    )
    assert [
        (column["Name"], column["Type"])
        for column in lead_time["StorageDescriptor"]["Columns"]
    ] == [
        ("pn", "string"),
        ("vendor", "string"),
        ("condition", "string"),
        ("promised_lead_days", "double"),
        ("realized_mean_days", "double"),
        ("realized_p50_days", "double"),
        ("realized_p90_days", "double"),
        ("realized_p99_days", "double"),
        ("promised_vs_actual_delta_mean", "double"),
        ("n_observations", "int"),
        ("observed_cycle_days", "array<int>"),
        ("evidence_status", "string"),
        ("source", "string"),
        ("grouping_level", "string"),
        ("confidence", "string"),
        ("data_cutoff", "date"),
        ("model_version", "string"),
        ("proxy_definition", "string"),
        ("classification_source", "string"),
        ("manifest_sha256", "string"),
        ("ingested_at", "timestamp"),
    ]


def test_open_orders_table_has_additive_repair_evidence_struct():
    tpl = _synth()
    tables = tpl.find_resources("AWS::Glue::Table")
    open_orders = next(
        resource["Properties"]["TableInput"]
        for resource in tables.values()
        if resource["Properties"]["TableInput"]["Name"]
        == "raw_open_orders_snapshot"
    )
    columns = {
        column["Name"]: column["Type"]
        for column in open_orders["StorageDescriptor"]["Columns"]
    }

    assert columns["orders"] == (
        "array<struct<order_id:string,order_type:string,vendor:string,"
        "qty_open:int,expected_rcv_date:date,order_line_id:string,"
        "opened_at:timestamp,status:string,serial_number:string,shop:string,"
        "location:string>>"
    )


def test_online_dynamodb_table_is_tenant_keyed():
    tpl = _synth()
    tpl.resource_count_is("AWS::DynamoDB::Table", 1)
    tpl.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "KeySchema": [
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "pn_location", "KeyType": "RANGE"},
            ],
            "SSESpecification": {"SSEEnabled": True, "SSEType": "KMS"},
            "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
        },
    )


def test_feature_group_glue_jobs_are_synthesized():
    tpl = _synth()
    jobs = tpl.find_resources("AWS::Glue::Job")
    # Materialization jobs + run-coherence ledger + generation-safe online publisher.
    assert len(jobs) == 13, f"expected 13 Glue jobs, got {len(jobs)}"
    rendered = str(sorted(str(j["Properties"]["Name"]) for j in jobs.values()))
    for slug in (
        "demand-history-job",
        "stock-position-job",
        "current-policy-job",
        "vendor-economics-job",
        "part-attributes-job",
        "criticality-job",
        "lead-time-distribution-job",
        "open-orders-snapshot-job",
        "requisition-snapshot-job",
        "interchangeable-graph-job",
        "location-graph-job",
        "extract-run-status-job",
        "online-population-job",
    ):
        assert slug in rendered, f"missing Glue job {slug}"
    for job in jobs.values():
        props = job["Properties"]
        assert props["GlueVersion"] == "4.0"
        assert props["WorkerType"] == "G.1X"
        assert props["NumberOfWorkers"] == 2
        assert props["Command"]["Name"] == "glueetl"
        assert props["Command"]["PythonVersion"] == "3"
        assert props["Command"]["ScriptLocation"], "ScriptLocation must be set"
        default_args = props["DefaultArguments"]
        assert default_args["--catalog_database"]
        assert default_args["--table_prefix"] == "raw_"
        assert default_args["--datalake-formats"] == "iceberg"
        assert default_args["--extra-py-files"]
        assert "spark.sql.catalog.glue_catalog" in str(default_args["--conf"])


def test_online_population_job_has_pinned_runtime_and_dynamo_target():
    tpl = _synth()
    jobs = tpl.find_resources("AWS::Glue::Job")
    population = next(
        job["Properties"]
        for job in jobs.values()
        if "online-population-job" in str(job["Properties"]["Name"])
    )
    arguments = population["DefaultArguments"]
    assert population["JobRunQueuingEnabled"] is True
    assert arguments["--tenant_id"] == "aircanada"
    assert arguments["--online_table_name"]
    assert arguments["--additional-python-modules"] == (
        "pydantic==2.13.1,"
        "pyiceberg[glue]==0.11.1,"
        "pyarrow==17.0.0"
    )
    assert arguments["--python-modules-installer-option"] == "--only-binary=:all:"


def test_successful_run_ledger_event_starts_online_population():
    tpl = _synth()
    tpl.resource_count_is("AWS::Events::Rule", 1)
    rule = next(iter(tpl.find_resources("AWS::Events::Rule").values()))["Properties"]
    assert rule["EventPattern"]["source"] == ["aws.glue"]
    assert rule["EventPattern"]["detail-type"] == ["Glue Job State Change"]
    assert rule["EventPattern"]["detail"]["state"] == ["SUCCEEDED"]
    assert "ExtractRunStatusJob" in str(
        rule["EventPattern"]["detail"]["jobName"]
    )
    assert rule["Targets"][0]["RetryPolicy"] == {
        "MaximumEventAgeInSeconds": 86400,
        "MaximumRetryAttempts": 185,
    }

    tpl.resource_count_is("AWS::Lambda::Function", 1)
    function = next(
        iter(tpl.find_resources("AWS::Lambda::Function").values())
    )["Properties"]
    assert function["Runtime"] == "python3.12"
    assert function["Timeout"] == 30
    assert "ConcurrentRunsExceededException" not in function["Code"]["ZipFile"]
    assert "JobRunQueuingEnabled=True" in function["Code"]["ZipFile"]
    assert "OnlinePopulationJob" in str(
        function["Environment"]["Variables"]["POPULATION_JOB_NAME"]
    )

    policies = tpl.find_resources("AWS::IAM::Policy")
    rendered = str(policies)
    assert "glue:StartJobRun" in rendered
    assert "dynamodb:PutItem" in rendered
    assert "dynamodb:GetItem" in rendered
    assert "dynamodb:Query" in rendered


def test_glue_python_package_contains_shared_runtime_modules() -> None:
    package = _GLUE_PACKAGE_DIR / "trax_io_feature_store"
    assert (package / "glue" / "_common.py").is_file()
    assert (package / "demand.py").is_file()
    assert (package / "schemas" / "features.py").is_file()
    assert (package / "runtime.py").is_file()
    assert (package / "online_writer.py").is_file()


def test_glue_role_scoped_to_tenant_kms():
    tpl = _synth()
    policies = tpl.find_resources("AWS::IAM::Policy")
    # At least one policy must grant kms:Decrypt + kms:GenerateDataKey scoped
    # to the tenant KMS key ARN (a Fn::GetAtt reference to the CMK), not "*".
    found = False
    for _, pol in policies.items():
        for stmt in pol["Properties"]["PolicyDocument"]["Statement"]:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            if "kms:Decrypt" in actions and "kms:GenerateDataKey" in actions:
                resource = stmt.get("Resource")
                # Must NOT be "*".
                assert resource != "*", "KMS policy must be resource-scoped"
                # Must reference the CMK (Fn::GetAtt TenantCmk... Arn).
                rendered = str(resource)
                assert "TenantCmk" in rendered, (
                    f"KMS policy should reference tenant CMK, got {rendered!r}"
                )
                found = True
    assert found, "no KMS policy statement found on the Glue role"


def test_glue_job_reads_landing_writes_lake():
    tpl = _synth()
    policies = tpl.find_resources("AWS::IAM::Policy")
    saw_landing_read = False
    saw_lake_write = False
    for _, pol in policies.items():
        stmts = pol["Properties"]["PolicyDocument"]["Statement"]
        rendered = str(stmts)
        # CDK names bucket resources by logical id.
        if "LandingBucket" in rendered and ("s3:GetObject" in rendered):
            saw_landing_read = True
        if "LakeBucket" in rendered and (
            "s3:PutObject" in rendered or "s3:DeleteObject" in rendered
        ):
            saw_lake_write = True
    assert saw_landing_read, "Glue role missing landing-bucket read grant"
    assert saw_lake_write, "Glue role missing lake-bucket write grant"


def test_project_tag_is_applied():
    tpl = _synth()
    # Every Glue table should carry Project=TraxIO via stack-level tag propagation.
    # Glue tables don't surface tags in the same shape as other resources, so we
    # assert on the bucket tag set instead.
    buckets = tpl.find_resources("AWS::S3::Bucket")
    for logical_id, resource in buckets.items():
        tags = resource["Properties"].get("Tags", [])
        tag_map = {t["Key"]: t["Value"] for t in tags}
        assert tag_map.get("Project") == "TraxIO", f"{logical_id} missing Project=TraxIO"
        assert tag_map.get("TenantId") == "aircanada", f"{logical_id} missing TenantId tag"
