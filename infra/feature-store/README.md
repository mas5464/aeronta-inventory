# Trax IO Feature Store — infra (AWS CDK, Python)

Per-tenant CDK stack for the native Iceberg and DynamoDB feature-store path.

## What this stack provisions (per tenant)

- **KMS CMK** (`alias/trax-io/<tenant_id>`) with annual rotation — envelope encryption for everything below (design §4.5).
- **S3 landing bucket** — drop zone for sub-project #1's nightly Parquet extracts + `manifest.json`. SSE-KMS, block-public-access, versioned.
- **S3 lake bucket** — backs the Iceberg tables. SSE-KMS, versioned.
- **Glue database + Iceberg feature/commit-ledger tables**. Every executable append verifies the native identity partition spec `(tenant_id, extract_date)`; `format-version = 2` enables time-travel.
- **DynamoDB online-features table** — `(tenant_id, pn_location)` key schema, PAY_PER_REQUEST, CMK-encrypted, PITR on (design §4.2).
- **Glue jobs** — materialization, run-coherence ledger, and pinned-dependency PyIceberg online population.
- **EventBridge + Lambda handoff** — an exact successful run-ledger state change starts the online population job.

## Glue jobs

PySpark materializers are packaged from
`services/feature-store/src/trax_io_feature_store/glue/` with the complete
feature-store source package. Jobs run on Glue 4.0/G.1X and use tenant-scoped
S3, KMS, and Glue Catalog permissions. The online population job is read-only
over the lake, writes generation-staged DynamoDB items, and runs with exact
Python 3.10-compatible PyIceberg/PyArrow/Pydantic pins.

Out of scope:
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
│   └── iceberg_schemas.py          # feature + ledger column schemas
└── tests/
    └── test_synth.py               # assertions.Template checks
```
