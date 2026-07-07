# Trax IO Inventory Optimizer — Installation & Deployment Guide

**Audience:** DevOps Engineers, IT Operations, Platform Architects  
**Last Updated:** 2026-07-07  
**Status:** Complete local/Docker reference; AWS deployment deferred to Phase 8

---

## Table of Contents

1. [Quick Start (Local Docker)](#quick-start-local-docker)
2. [Architecture Overview](#architecture-overview)
3. [Local Development Setup](#local-development-setup)
4. [Production AWS Deployment](#production-aws-deployment)
5. [Real eMRO Oracle Integration](#real-emro-oracle-integration)
6. [Kafka Broker Setup](#kafka-broker-setup)
7. [Configuration & Secrets Management](#configuration--secrets-management)
8. [Monitoring & Observability](#monitoring--observability)
9. [Troubleshooting](#troubleshooting)

---

## Quick Start (Local Docker)

### 1. Prerequisites

- **macOS 12+** or **Linux** (Intel/ARM-based Docker runtime)
- **Docker Desktop** 4.20+ with **Docker Compose** v2.15+
- **Git**
- **Python 3.12+** (for local dev; Docker runs 3.14)
- **Java 21** (for `services/emro-writeback-java`; local builds only)
- **Node 18+** (for `apps/web` frontend; builds in Docker)

### 2. One-Command Local Deploy

```bash
cd /path/to/Inventory\ Opmimizer/.claude/worktrees/nervous-swirles-424ddf

# Spin up the full Trax IO stack locally
docker compose up --build

# Wait ~30s for services to stabilize
# Open http://localhost:8089 (UI) in your browser
# BFF API is at http://localhost:8001 (debug)
```

### 3. What Just Came Up

| Service | Port | Purpose | Status |
|---|---|---|---|
| **web** (nginx + React) | 8089 | User-facing Trax IO Planner UI | ✓ Ready at `/` |
| **bff** (FastAPI + Planner Store) | 8001 | Backend-for-frontend; recommendation queue & dashboards | ✓ Ready at `/v1/*` |
| **writeback-java** (Quarkus) | 8090 | Real eMRO write-back service (optional; needs real Oracle) | ✓ No auth required locally |
| **redpanda** (Kafka-compatible) | 19092 | Event streaming for write-back domain events | ✓ Topics: `optimizer.writeback.v1`, `.results.v1`, `.dlq.v1` |
| **oracle19c** | 1521 | Shared eMRO Oracle database (part of multi-project setup) | ⚠ **NEVER touched by this project** |

### 4. Verify Everything is Working

```bash
# Health check: list all running services
docker compose ps

# Check BFF is responding
curl -s http://localhost:8001/health | jq .

# Open the UI and log in with any JWT
# (dev mode requires no auth; the app will accept bearer tokens if sent)
```

### 5. Stopping & Cleanup

```bash
# Stop all services (volumes/data persist)
docker compose down

# Remove all data (reset to clean slate)
docker compose down -v

# IMPORTANT: NEVER stop the oracle19c container directly
# It is shared across all projects and managed separately
```

---

## Architecture Overview

Trax IO runs as a **multi-service orchestration** with clear separation of concerns:

```
┌──────────────────────────────────────────────────────────────┐
│                        UI Layer (nginx)                       │
│                    (8089) React / Tailwind                    │
└────────────────────────┬─────────────────────────────────────┘
                         │ same-origin proxy
┌────────────────────────▼─────────────────────────────────────┐
│              Backend-for-Frontend (BFF)                       │
│    (8001) FastAPI, Planner Store, PlannerApp                │
│         - Recommendation queue (approval loop)                │
│         - Dashboard KPIs & drill panels                       │
│         - Reports (Business Value Report)                     │
│         - History & rollback ledger                           │
└────────────────────────┬─────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
    ┌────▼──────┐  ┌────▼──────┐  ┌────▼──────┐
    │Feature    │  │Event      │  │Writeback  │
    │Store      │  │Publisher  │  │Service    │
    │(offline)  │  │(events)   │  │(eMRO)     │
    └────┬──────┘  └────┬──────┘  └────┬──────┘
         │              │              │
    ┌────▼──────────┐  │         ┌────▼──────────┐
    │Iceberg Lake   │  │         │Oracle eMRO    │
    │(S3 + Glue)    │  │         │WRITEBACK_     │
    │via DynamoDB   │  │         │LEDGER         │
    │(online)       │  │         │(history)      │
    └───────────────┘  │         └───────────────┘
                   ┌───▼──────┐
                   │  Kafka   │
                   │(Redpanda)│
                   └──────────┘
```

**Key Design Principles:**

- **No AWS yet** — v1 runs on Docker/local; CDK is written but not deployed (phase 8)
- **Oracle-first data** — nightly extract from eMRO; no direct eMRO queries during optimization
- **Write-back audited** — all changes tracked in `WRITEBACK_LEDGER` with version chaining
- **Kafka-driven events** — domain events (requisition created, transfer pending) emit to topics
- **Feature Store is load-bearing** — if it slips, everything downstream slips

---

## Local Development Setup

### 1. Clone & Navigate

```bash
git clone https://github.com/mas5464/trax-io-inventory-optimizer.git
cd Inventory\ Opmimizer/.claude/worktrees/nervous-swirles-424ddf
```

### 2. Install Python Dependencies (for local test/lint runs)

```bash
# Feature Store
cd services/feature-store && uv sync --extra dev && cd ../..

# Recommendation Engine
cd services/recommendation-engine && uv sync --extra dev && cd ../..

# Agent Spine (the orchestration core)
cd services/agent-spine && uv sync --extra dev --extra bff --extra bvr && cd ../..

# Forecasting models
cd services/forecasting && uv sync --extra dev && cd ../..

# Event Publisher
cd services/event-publisher && uv sync --extra dev && cd ../..

# Nightly Extract (eMRO data ingestion)
cd tools/nightly-extract && uv sync --extra dev && cd ../..
```

### 3. Install Node Dependencies (for UI)

```bash
cd apps/web
npm install
npm run build
```

### 4. Java Writeback Service (if touching `services/emro-writeback-java`)

```bash
cd services/emro-writeback-java
mvn clean test -Dnet.bytebuddy.experimental=true
# Note: -Dnet.bytebuddy.experimental=true is needed for JDK 25+
```

### 5. Run Tests Locally (Before Committing)

```bash
# Python packages (pick the ones you edited)
cd services/feature-store && uv run --extra dev pytest
cd services/recommendation-engine && uv run --extra dev pytest --extra api
cd services/agent-spine && uv run --extra dev pytest --extra bff --extra bvr

# UI
cd apps/web && npm test && npm run lint

# Java
cd services/emro-writeback-java && mvn test -Dnet.bytebuddy.experimental=true
```

### 6. Running CLI Tools Locally

```bash
# Extract sample recommendation data
cd services/recommendation-engine
uv run trax-io-reco run --data-file examples/seed.json

# Run the full offline orchestration (extract → recommend → guardrail → writeback)
cd services/agent-spine
uv run trax-io-spine run --extract-dir ../recommendation-engine/examples/extract_sample \
  --tenant acme \
  --dry-run \
  --shadow

# Replay events from a JSONL file
uv run trax-io-spine ingest --extract-dir ... --tenant acme --events events.jsonl --dry-run
```

---

## Production AWS Deployment

### ⚠️ Status: Code Written, Not Deployed (Phase 8)

The following CDK stacks are **synthesis-complete** but **not deployed to any AWS account**:

- `infra/feature-store/app.py` — Iceberg tables, Glue jobs, DynamoDB online layer (per tenant)
- `infra/observability-soc2/app.py` — CloudTrail, Audit Manager, X-Ray, log groups (account-wide + per tenant)

### 1. Prerequisites for AWS Deployment

- **AWS Account:** `TraxAi` (or dedicated account per org policy)
- **AWS CLI 2.x** configured with credentials
- **AWS CDK 2.x** installed: `npm install -g aws-cdk`
- **Python 3.12+** with `pip` (CDK uses Python runtime)
- **IAM role** with permissions:
  - `ec2:*`, `s3:*`, `kms:*`, `dynamodb:*`, `glue:*`, `iam:*`, `cloudtrail:*`, `cloudwatch:*`
  - (Principle: least privilege; refine to specific resource ARNs for production)

### 2. Synthesize Stacks (No Deployment Yet)

```bash
cd infra/feature-store

# Validate CDK can synthesize the stack (outputs CloudFormation template)
cdk synth --context tenants='["aircanada","united"]'
# → outputs `cdk.out/` with CloudFormation JSON

# Review the template (read-only)
cat cdk.out/TraxIO-FeatureStore-aircanada.template.json | jq '.Resources | keys' | head -10
```

### 3. When Ready to Deploy (Phase 8 onwards)

```bash
# Bootstrap the AWS account (one-time, creates staging S3 bucket for CDK)
cdk bootstrap aws://ACCOUNT_ID/REGION

# Deploy a single tenant's stack
cdk deploy TraxIO-FeatureStore-aircanada --require-approval=any-change

# Deploy multiple tenants
cdk deploy --context tenants='["aircanada","united","delta"]'

# Destroy a stack (careful!)
cdk destroy TraxIO-FeatureStore-aircanada
```

### 4. IaC Configuration in YAML/CDK

**AWS Resources Defined in CDK (Python):**

```python
# infra/feature-store/app.py excerpt
from aws_cdk import (
    aws_s3 as s3,
    aws_kms as kms,
    aws_glue as glue,
    aws_dynamodb as dynamodb,
)

class FeatureStoreStack(Stack):
    def __init__(self, scope, id, tenant_id, **kwargs):
        super().__init__(scope, id, **kwargs)
        
        # KMS CMK per tenant
        cmk = kms.Key(self, "TenantKey",
            enable_key_rotation=True,
            pending_window=Duration.days(7),
        )
        
        # S3 landing zone (nightly extract drop)
        landing = s3.Bucket(self, "LandingBucket",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=cmk,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
        
        # DynamoDB online feature cache
        online_table = dynamodb.Table(self, "OnlineFeatures",
            partition_key=Attribute(name="tenant_id", type=AttributeType.STRING),
            sort_key=Attribute(name="pn_location", type=AttributeType.STRING),
            billing_mode=BillingMode.PAY_PER_REQUEST,
            encryption=TableEncryption.CUSTOMER_MANAGED,
            encryption_key=cmk,
            point_in_time_recovery=True,
        )
```

**To export as Terraform (optional future):**

```bash
# CDK supports Terraform output
cdk synth --template-format=yaml > infra-as-code.yaml
```

### 5. Multi-Region Replication (Target Design, Not Yet Coded)

```yaml
# Target configuration (design-only; CDK not yet updated)
regions:
  primary: us-east-1
  disaster-recovery: us-west-2
replication:
  s3_buckets: replicate-with-kms-key-replication
  dynamodb: global-tables-with-backup-retention-7yr
  cloudtrail: multi-region-enabled
```

---

## Real eMRO Oracle Integration

### 1. Connection Details

```bash
# Real eMRO (non-containerized instance at a customer site)
Host:     <customer-oracle-server>
Port:     1521
Service:  <SID or service name>
User:     ODB
Password: <from customer secrets management>

# Local development (oracle19c container shared across projects)
Host:     localhost
Port:     1521
Service:  LOCAL
User:     ODB
Password: ODB
```

### 2. JDBC Connection String

```properties
# Local dev (in docker-compose.yml)
WRITEBACK_DB_URL=jdbc:oracle:thin:@localhost:1521/LOCAL

# Production (env var or secrets manager)
WRITEBACK_DB_URL=jdbc:oracle:thin:@customer-oracle.internal:1521/PRODUCTION
```

### 3. Schema Setup (Flyway Migrations)

The Java write-back service **never modifies eMRO tables**. Flyway manages **only** `WRITEBACK_LEDGER`:

```sql
-- services/emro-writeback-java/src/main/resources/db/migration/V1__Create_Writeback_Ledger.sql
-- Flyway runs this exactly once per database

CREATE TABLE writeback_ledger (
    id              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id       VARCHAR2(64) NOT NULL,
    pn              VARCHAR2(32) NOT NULL,
    location        VARCHAR2(16) NOT NULL,
    version         NUMBER NOT NULL,
    parent_version  NUMBER,
    domain          VARCHAR2(16) NOT NULL,  -- STOCK_LEVEL, REQUISITION, TRANSFER
    created_ref     VARCHAR2(64),           -- requisition/order number for duplicates
    idempotency_key VARCHAR2(255) NOT NULL,
    new_values      CLOB NOT NULL,          -- JSON: {rop, eoq, ss, max}
    old_values      CLOB,                   -- JSON or NULL for first write
    message         VARCHAR2(255),          -- outcome message
    created_at      TIMESTAMP NOT NULL,
    created_by      VARCHAR2(128),
    outcome         VARCHAR2(32) NOT NULL,  -- WRITTEN, SKIPPED_DUPLICATE, ERROR
    
    UNIQUE (tenant_id, idempotency_key),
    UNIQUE (tenant_id, pn, location, version),
    CONSTRAINT fk_parent_version FOREIGN KEY (tenant_id, pn, location, parent_version)
        REFERENCES writeback_ledger (tenant_id, pn, location, version)
);
```

**Why no DDL on eMRO tables?**

- eMRO is a production system; schema changes there go through customer change management
- The ledger is our "system of record" for audit; it lives in a controlled partition
- Reads of `PN_INVENTORY_LEVEL` are read-only (validation only)

### 4. Smoke Test Against Real Oracle

```bash
# Set environment variables (only runs if all 5 are set)
export EMRO_SMOKE_DB_URL="jdbc:oracle:thin:@localhost:1521/LOCAL"
export EMRO_SMOKE_DB_USER="ODB"
export EMRO_SMOKE_DB_PASSWORD="ODB"
export EMRO_SMOKE_PN="E-ROT"
export EMRO_SMOKE_LOCATION="JFK"

# Run the smoke test
cd services/emro-writeback-java
mvn test -Dtest=EmroSchemaSmokeTest -Dnet.bytebuddy.experimental=true

# Expected output:
# [INFO] Tests run: 1, Failures: 0, Errors: 0
# Smoke test verifies:
#  ✓ Oracle connectivity
#  ✓ WRITEBACK_LEDGER table exists
#  ✓ PN_INVENTORY_LEVEL table exists
#  ✓ ORDER_HEADER, ORDER_DETAIL, REQUISITION_HEADER tables exist
#  ✓ Package PKG_APPLICATION_FUNCTION exists (needed for order/requisition numbers)
```

### 5. Operational Procedures

**Backup Strategy:**

```bash
# eMRO Oracle uses customer's backup strategy; we protect only WRITEBACK_LEDGER
# Daily backup of WRITEBACK_LEDGER (example: customer-run)
expdp ODB FILE=writeback_ledger_backup_%DATE%.dmp TABLES=writeback_ledger
```

**Monitoring:**

```bash
# Check ledger size (weekly)
SELECT COUNT(*) as total_rows,
       COUNT(DISTINCT tenant_id) as tenants,
       ROUND(SUM(dbms_lob.getlength(new_values))/1024/1024, 2) as size_mb
FROM writeback_ledger;

# Monitor for stuck/error rows
SELECT tenant_id, pn, location, outcome, message, COUNT(*)
FROM writeback_ledger
WHERE outcome = 'ERROR'
GROUP BY tenant_id, pn, location, outcome, message;
```

---

## Kafka Broker Setup

### 1. Local Development (Redpanda in Docker Compose)

```yaml
# Already in docker-compose.yml
redpanda:
  image: docker.redpanda.com/redpanda:latest
  ports:
    - "19092:19092"  # internal bootstrap
    - "29092:29092"  # external bootstrap
  environment:
    REDPANDA_BROKERS: redpanda:9092
  command: >
    redpanda start
    --mode dev-container
    --node-id 0
    --advertised-kafka-api 0.0.0.0:19092
```

### 2. Production Kafka (AWS MSK - Not Yet Deployed)

**Target configuration (Phase 8+):**

```python
# infra/observability-soc2/app.py (future addition)
from aws_cdk import aws_msk as msk

class KafkaStack(Stack):
    def __init__(self, scope, id, vpc, **kwargs):
        super().__init__(scope, id, **kwargs)
        
        broker_subnets = [vpc.private_subnets[0], vpc.private_subnets[1]]
        
        kafka_cluster = msk.Cluster(self, "TraxIOKafka",
            kafka_version=msk.KafkaVersion.V3_7_X,
            number_of_broker_nodes=3,
            instance_type=ec2.InstanceType("kafka.m5.large"),
            vpc=vpc,
            vpc_subnets=SubnetSelection(subnets=broker_subnets),
            encryption_in_transit=msk.EncryptionInTransit(
                enabled=True,
                client_broker=msk.ClientBrokerEncryption.TLS,
            ),
            encryption_at_rest=msk.EncryptionAtRest(
                enabled=True,
                key_management_service=cmk,  # Customer-managed KMS key
            ),
            logging=msk.BrokerLogs(
                cloudwatch_logs=msk.CloudWatchLogsLogging(
                    enabled=True,
                    log_group=log_group,
                ),
            ),
        )
```

### 3. Topics & Partitioning

```bash
# Create topics locally (done automatically by the services; shown for reference)
kafka-topics.sh --bootstrap-server localhost:19092 --create \
  --topic optimizer.writeback.v1 \
  --partitions 3 \
  --replication-factor 1 \
  --config retention.ms=604800000  # 7 days

kafka-topics.sh --bootstrap-server localhost:19092 --create \
  --topic optimizer.writeback.results.v1 \
  --partitions 3 \
  --replication-factor 1 \
  --config retention.ms=259200000  # 3 days

kafka-topics.sh --bootstrap-server localhost:19092 --create \
  --topic optimizer.writeback.dlq.v1 \
  --partitions 1 \
  --replication-factor 1
```

| Topic | Partition Count | Retention | Purpose |
|---|---|---|---|
| `optimizer.writeback.v1` | 3 | 7 days | Inbound action records (stock level, requisition, transfer) |
| `optimizer.writeback.results.v1` | 3 | 3 days | Per-row outcomes (for replay) |
| `optimizer.writeback.dlq.v1` | 1 | 30 days | Failed messages after retry |

### 4. Consumer Group Configuration

```properties
# services/emro-writeback-java application.properties
quarkus.kafka.devservices.enabled=true
quarkus.kafka.bootstrap.servers=localhost:19092
quarkus.kafka.group.id=trax-io-writeback-consumer
quarkus.kafka.auto.offset.reset=earliest
```

---

## Configuration & Secrets Management

### 1. Local Development (.env Pattern)

```bash
# Create a .env file (never commit this)
cat > .env << EOF
# Database
WRITEBACK_DB_URL=jdbc:oracle:thin:@localhost:1521/LOCAL
WRITEBACK_DB_USER=ODB
WRITEBACK_DB_PASSWORD=ODB

# Kafka
KAFKA_BOOTSTRAP_SERVERS=localhost:19092

# JWT (dev-only, insecure keys for testing)
WRITEBACK_JWT_ISSUER=https://local.test/issuer
WRITEBACK_JWT_PUBLIC_KEY_FILE=/path/to/public.pem

# eMRO
WRITEBACK_EMRO_COMPANY=TRAX
EOF

# docker-compose will inject these into containers
docker compose up
```

### 2. Production AWS Secrets Manager

```python
# Example: retrieving secrets from AWS Secrets Manager (Phase 8+)
import json
import boto3

secrets = boto3.client('secretsmanager', region_name='us-east-1')

def get_db_credentials(tenant_id):
    response = secrets.get_secret_value(
        SecretId=f'/trax-io/{tenant_id}/db-credentials'
    )
    return json.loads(response['SecretString'])

# Usage in app startup
db_creds = get_db_credentials('aircanada')
os.environ['WRITEBACK_DB_URL'] = db_creds['jdbc_url']
os.environ['WRITEBACK_DB_USER'] = db_creds['user']
os.environ['WRITEBACK_DB_PASSWORD'] = db_creds['password']
```

### 3. JWT Signing Key Rotation (Dev vs. Production)

**Local Dev (Public):**

```bash
# For local testing, generate a keypair (insecure, for dev only)
openssl genrsa -out private.pem 2048
openssl rsa -in private.pem -pubout -out public.pem

# Set in .env
WRITEBACK_JWT_PUBLIC_KEY_FILE=/path/to/public.pem
```

**Production (Secure):**

```bash
# Store the public key in AWS Secrets Manager
aws secretsmanager create-secret \
  --name /trax-io/jwt-public-key \
  --secret-string file://public.pem

# Application fetches at startup
public_key = aws_secrets_manager.get_secret_value(
    SecretId='/trax-io/jwt-public-key'
)['SecretString']
```

### 4. Environment Variable Precedence

1. **Docker Compose** `.env` file (local)
2. **System environment** (`export VAR=value`)
3. **application.properties** / application.yaml (app defaults)
4. **AWS Secrets Manager** (production)

---

## Monitoring & Observability

### 1. Logs (CloudWatch in Production; Docker Compose Locally)

```bash
# View service logs locally
docker compose logs -f bff              # BFF/API layer
docker compose logs -f writeback-java   # Write-back service
docker compose logs -f web              # React UI (nginx)

# Filter by error level
docker compose logs --tail 50 bff | grep ERROR
```

### 2. Metrics (OpenTelemetry → CloudWatch/Grafana)

**Local dev:** metrics are logged to stdout (Micrometer)

```bash
# Watch for request counts in BFF
docker compose logs bff | grep 'writeback.items'
```

**Production (Phase 8+):**

```yaml
# infra/observability-soc2/app.py emits OTEL config
otel_collector:
  processors:
    batch: {}
  exporters:
    logging:
      loglevel: debug
    otlp:
      endpoint: 0.0.0.0:4317  # gRPC
    datadog:
      api_key: ${DATADOG_API_KEY}  # optional
  service:
    pipelines:
      traces:
        receivers: [otlp]
        processors: [batch]
        exporters: [otlp, logging]
      metrics:
        receivers: [otlp]
        processors: [batch]
        exporters: [otlp, logging]
```

### 3. Tracing (X-Ray in Production)

**Enable trace context in app:**

```python
# services/agent-spine/src/trax_io_spine/observability.py
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.exporter.jaeger.thrift import JaegerExporter

tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("recommend_batch"):
    # ... orchestration code
    pass
```

### 4. Audit Logging

Every write to eMRO is logged to `WRITEBACK_LEDGER`:

```sql
SELECT id, tenant_id, pn, location, domain, created_at, created_by, outcome, message
FROM writeback_ledger
WHERE created_at > SYSDATE - 1
ORDER BY created_at DESC;
```

---

## Troubleshooting

### Issue: "connect ECONNREFUSED 127.0.0.1:19092"

**Symptom:** UI loads but BFF cannot reach Kafka

**Fix:**
```bash
# 1. Check Kafka is running
docker compose ps redpanda
# Status should be "Up"

# 2. Restart Kafka
docker compose restart redpanda

# 3. Verify topics exist
docker compose exec redpanda rpk topic list
# Should list: optimizer.writeback.v1, results.v1, dlq.v1
```

### Issue: "ORA-00001: unique constraint (WRITEBACK_LEDGER.UQ_WRITEBACK_IDEMPOTENCY) violated"

**Symptom:** Duplicate idempotency key rejected (expected behavior for retries)

**Expected:** The API returns 409 Conflict with the original `CREATED_REF`

**If stuck:** Check the ledger for a stale entry:
```sql
SELECT * FROM writeback_ledger WHERE idempotency_key = 'your-key-here';
```

### Issue: "ORA-18716: {0} not in any time zone.DATE"

**Symptom:** Java service fails reading `CREATED_AT` from real Oracle 19c

**Fix:** Run the service with `-Duser.timezone=UTC`:
```bash
export MAVEN_OPTS="-Dnet.bytebuddy.experimental=true -Duser.timezone=UTC"
cd services/emro-writeback-java && mvn package
```

### Issue: Docker container stops after 10s

**Check logs:**
```bash
docker compose logs writeback-java --tail 20
# Look for: initialization error, port conflict, missing env var
```

**Common causes:**
- `WRITEBACK_DB_URL` not set → service can't connect to Oracle
- Flyway migration failed → check Oracle connectivity
- Port 8090 already in use → `lsof -i :8090` and kill the process

### Issue: "Cannot allocate memory"

**Symptom:** Docker build OOMs during GradlePlugin / NumPy compilation

**Fix:**
```bash
# Increase Docker memory limit
# Docker Desktop: Preferences → Resources → Memory: 8GB+

# or build outside Docker
cd services/forecasting && uv sync --extra dev && pytest
```

---

## Next Steps

1. **For local development:** Run `docker compose up --build` and start making changes
2. **For AWS deployment:** Wait for Phase 8; review `infra/` CDK stacks in the meantime
3. **For real eMRO integration:** Coordinate with customer on database access & backup strategy
4. **For production readiness:** See [05-aws-infrastructure-guide.md](./guides-src/05-aws-infrastructure-guide.md) for full deployment strategy

---

## References

- [Full Feature Guide](./guides-src/04-full-feature-guide.md) — What runs today & how
- [Architecture Guide](./guides-src/01-architecture-guide.md) — Design principles
- [AWS Infrastructure Guide](./guides-src/05-aws-infrastructure-guide.md) — What's coded for Phase 8+
- [Integration Handoff Guide](./guides-src/03-integration-handoff-guide.md) — Connecting to customer systems
- [Design Document](./design/2026-04-14-trax-io-inventory-optimizer-design.md) — Authoritative design spec
- [ROADMAP](../ROADMAP.md) — Feature phases & timeline
