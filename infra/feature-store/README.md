# Trax IO Feature Store — infra (AWS CDK, Python)

Phase 1 scaffold for sub-project #2. **Synth only — do not `cdk deploy` from this scaffold.** Phase 8 of the plan introduces the per-tenant deploy pipeline via OIDC into the Trax AWS deployment account (`TraxAi`).

## What this stack provisions (per tenant)

- **KMS CMK** (`alias/trax-io/<tenant_id>`) with annual rotation — envelope encryption for everything below (design §4.5).
- **S3 landing bucket** — drop zone for sub-project #1's nightly Parquet extracts + `manifest.json`. SSE-KMS, block-public-access, versioned.
- **S3 lake bucket** — backs the Iceberg tables. SSE-KMS, versioned.
- **Glue database + 10 Iceberg tables** (one per v1 feature group from design §4.2). Partitioned on `(tenant_id, extract_date)`, `format-version = 2` for time-travel (SOC 2 reproducibility per design §4.2).
- **DynamoDB online-features table** — `(tenant_id, pn_location)` key schema, PAY_PER_REQUEST, CMK-encrypted, PITR on (design §4.2).

## Glue jobs (Phase 2)

Phase 2 ships the first Glue ETL job as a template slice:

| Job name | Consumes | Produces |
|---|---|---|
| `<tenant>-demand-history-job` | `manifest.json` + `demand_history_rotables.json` + `demand_history_expendables.json` (from the landing bucket) | Rows in the `demand_history` Iceberg table (`glue_catalog.trax_io.demand_history`), partitioned by `(tenant_id, extract_date)` |

The PySpark script is packaged as a CDK S3 asset from
`services/feature-store/src/trax_io_feature_store/glue/demand_history_job.py`.
The job runs on Glue 4.0, `G.1X` worker type, 2 workers. Its IAM role is
least-privilege: read on the landing bucket, read+write on the lake bucket,
tight `kms:Decrypt` / `kms:GenerateDataKey` on the tenant CMK, and
catalog access scoped to this tenant's Glue database.

The remaining 9 feature-group jobs (`causal_utilization`,
`lead_time_distribution`, ...) follow the same pattern.

Out of scope for Phase 1 (arrive in later phases per the plan):
- Remaining 9 Glue ETL jobs (Phase 2 continued)
- Kinesis streams + event-lane CDC (Phase 5)
- OpenLineage / Marquez hooks (Phase 7)
- Audit-bucket Object Lock + cross-region replication (Phase 7 task 30)
- Multi-tenant deploy pipeline (Phase 8)

## Dev setup

```bash
cd infra/feature-store
uv sync --extra dev
uv run pytest             # synth assertions
uv run cdk synth          # emit CloudFormation to ./cdk.out
```

`cdk` itself is expected to be on your PATH (`npm i -g aws-cdk` or similar). The `pytest` suite synthesizes via `aws-cdk-lib` directly so it does NOT require the `cdk` CLI to run.

## Conventions

- Every resource tagged `Project=TraxIO` (enforced at `App` level) and `TenantId=<tenant>` (at `Stack` level).
- AWS account: `TraxAi`.
- No AWS API calls from this repo. `cdk synth` only.

## Layout

```
infra/feature-store/
├── pyproject.toml
├── README.md
├── cdk.json
├── app.py                          # CDK app entrypoint
├── stacks/
│   ├── __init__.py
│   ├── feature_store_stack.py      # KMS + S3 + Glue + DynamoDB per tenant
│   └── iceberg_schemas.py          # 10 feature-group column schemas
└── tests/
    └── test_synth.py               # assertions.Template checks
```
