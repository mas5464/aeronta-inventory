---
title: "Trax IO — AWS Infrastructure Guide"
subtitle: "Target Architecture, What's Coded Today, and the Path to Deploy"
author: "Miguel Sosa, VP Head of Innovation · Trax"
date: "2026-07-06"
---

\newpage

# 1. Purpose and the One Fact That Matters Most

The Technical Architecture Guide describes Trax IO's target AWS footprint. The
Full Feature Guide describes the application logic that runs today, entirely
outside AWS. This guide sits between them: it inventories exactly what
infrastructure-as-code has been written, what it would provision, and what
separates "the CDK synthesizes clean CloudFormation" from "this is running in an
AWS account."

**Zero AWS resources are provisioned today.** Every stack below is explicitly
marked "synth only" in its own source comments. `infra/feature-store/app.py`
states it directly: *"Do NOT `cdk deploy` from this scaffold — Phase 8 of the
plan introduces the per-tenant deploy pipeline with OIDC into the Trax AWS
deployment account."* This guide takes that constraint at face value throughout
— nothing here should be read as "already deployed."

# 2. Status Legend

| Symbol | Meaning |
|---|---|
| 🎯 | Target design only — described in the Architecture Guide, no code written toward it |
| 🧱 | Real CDK written, `cdk synth`-tested in CI, **not deployed** |
| 🚀 | Deployed to a real AWS account |

No item in this document currently carries 🚀.

\newpage

# 3. Account & Tenancy Model — 🎯

Design calls for a single shared AWS account (`TraxAi`) for all of v1, with
per-tenant isolation enforced through KMS keys, Cedar policies, IAM role
boundaries, and per-tenant CDK stacks — not through separate AWS accounts per
tenant. This is a deliberate simplicity choice for v1 scale.

The per-tenant *stack* pattern is already real, not just designed: both CDK
packages below instantiate one stack per tenant from a `tenants` context list
(`infra/feature-store/app.py` defaults to `["aircanada"]` when no context is
supplied), producing stack names like `TraxIO-FeatureStore-aircanada`.

\newpage

# 4. Data Plane (Per Tenant) — 🧱 `infra/feature-store`

`FeatureStoreStack` (11 tests, `cdk synth` clean) provisions, per tenant:

| Resource | Configuration |
|---|---|
| KMS CMK | `alias/trax-io/<tenant_id>`, key rotation enabled, `RETAIN` on stack deletion |
| S3 landing bucket | SSE-KMS (tenant CMK), versioned, all public access blocked, SSL-enforced — the nightly-extract drop zone |
| S3 lake bucket | Same encryption/access posture — backs the Iceberg tables below |
| Glue database | `trax_io_lake_<tenant_id>` |
| 10 Iceberg tables | One per v1 feature group (design §4.2), `EXTERNAL_TABLE`, format-version 2 (time-travel), partitioned by `(tenant_id, extract_date)`, located under the lake bucket |
| 10 Glue ETL jobs | One PySpark job per feature group, `glue_version 4.0`, `G.1X` × 2 workers, each with a least-privilege IAM role scoped to that tenant's KMS key + landing/lake buckets + Glue catalog path (not the broad managed policy alone) |
| DynamoDB online table | `PK=tenant_id`, `SK=pn_location`, pay-per-request, customer-managed KMS encryption, point-in-time recovery |

The Glue job definitions point at real PySpark source already checked into
`services/feature-store/.../glue/<group>_job.py` — the CDK stack fails its own
synth if that sibling file is missing, so the infrastructure code and the
application code it deploys are load-bearing on each other, not independently
fictional.

**Deliberately excluded from this stack today** (target-only, later phases per
the file's own header comment): Glue streaming/triggers, Kinesis data streams,
EventBridge rules, and cross-region replication. The stack synthesizes
everything needed to unblock the Agent Spine against real ARNs; the event-driven
plumbing arrives with the Event Plane (§7).

\newpage

# 5. Control Plane — Observability & SOC 2 — 🧱 `infra/observability-soc2`

`ObservabilitySoc2Stack` (40 tests across stack/IAM-helper/tenant-spec suites,
`cdk synth` clean) splits into account-wide singletons and per-tenant resources.

**Singletons (one per AWS account):**

| Resource | Configuration |
|---|---|
| Audit KMS key | `alias/trax-io/audit`, key rotation enabled |
| Audit S3 bucket | **Object Lock, Compliance mode** (non-bypassable, not Governance), 2,557-day (7-year) default retention, versioned, SSE-KMS, all public access blocked |
| CloudTrail Lake event data store | Multi-region, 7-year retention, termination-protected |
| AWS Audit Manager assessment | SOC 2 Type II framework, scoped to `s3`/`kms`/`cloudtrail`/`iam`/`cloudwatch`/`logs`, `PROCESS_OWNER` role bound to a `TraxIoSecOps` IAM role |
| OpenTelemetry Collector | Fargate task (512 CPU / 1024 MiB) in a 2-AZ VPC with a NAT gateway, task role with `AWSXRayDaemonWriteAccess` + `CloudWatchAgentServerPolicy`, running the public `aws-observability/aws-otel-collector` image |

**Per tenant:** a KMS CMK (explicit 365-day rotation period) and a CloudWatch log
group (`/trax-io/<tenant_id>`, 1-year retention, encrypted with that tenant's
CMK) — each exported via a frozen `CfnOutput` naming contract,
`TraxIo-<tenant_id>-TenantKmsArn` and `TraxIo-<tenant_id>-TenantLogGroupArn`,
that downstream stacks (feature-store, nightly-extract) are meant to consume via
`Fn::ImportValue`. Changing these literal strings is a breaking change across
stacks.

**Two gaps the code itself flags, verbatim:**

1. The audit bucket carries an explicit metadata note: *"PROD: move to dedicated
   Trax audit account; Phase 1/2 synth keeps in-stack."* The design's dual-book
   pattern calls for the immutable audit trail to live in a separate AWS account
   from the operational stacks — not yet true here.
2. The SOC 2 framework ID is a literal placeholder
   (`00000000-0000-0000-0000-000000000000`) until a real deploy discovers the
   actual UUID via `aws auditmanager list-assessment-frameworks`.

\newpage

# 6. Compute / Agent Plane — 🎯 Nothing Coded

The design calls for Bedrock AgentCore Runtime hosting a Supervisor and six
specialist subagents (Strands), AgentCore Memory/Identity/Gateway/Observability
for tenant-scoped agent state, and SageMaker for forecasting inference. **None
of this exists as code anywhere in the repository.** There is no Bedrock SDK
usage, no Strands import, no SageMaker endpoint definition, no AgentCore
construct, in any package.

What stands in for it today, entirely outside AWS: `services/agent-spine`'s
`SupervisorOrchestrator` is a deterministic, framework-free Python class
(ADR-0001's explicit design choice — kept framework-free specifically so it can
be rewritten as a single file if a Strands migration ever happens), and
`services/forecasting` runs its three regime-specific models
(`statsforecast`/sklearn/scipy) as an in-process Python library with zero
SageMaker dependency. This is a real, tested, working substitute for the
orchestration and inference logic — but it is not the AWS compute plane the
design specifies, and migrating to Bedrock AgentCore + SageMaker is entirely
unstarted work, not a configuration change.

\newpage

# 7. Event Plane — 🎯 Nothing Coded

Design calls for an eMRO-side outbox-and-drainer (Java Spring Boot add-on)
pushing seven domain event kinds over mTLS to an API Gateway endpoint, fanning
out through EventBridge to per-tenant Kinesis streams, with a Glue streaming job
for Iceberg CDC materialization, a Lambda consumer for the DynamoDB online
layer, and an EventBridge-triggered Step Function for sub-5-minute "hot parts"
recomputes. No Kinesis stream, EventBridge rule, Lambda consumer, or Step
Function is defined in either CDK package today.

`services/event-publisher` implements the canonical event schema and a
conformance test harness against a `fake_event_endpoint`, and `agent-spine`'s
`ingest` CLI command replays a JSONL batch of canonical events through the real
consumer-side pipeline (dedup → canonical adapter → recompute → writeback).
Together these prove the **contract** the real event plane must satisfy — they
do not stand up any of the AWS messaging infrastructure itself.

\newpage

# 8. Writeback / eMRO Integration — 🎯 Contract Designed, 🧱 Reference Implementation Only

The target integration is a real REST surface inside eMRO
(`PUT/GET/POST /v1/tenants/{tenant_id}/inventory-level/...`), authenticated by
mutual TLS plus a short-lived bearer JWT, documented in the Integration Handoff
Guide. No such endpoint exists against a real eMRO instance today, and no AWS
resource is involved in this integration surface at all — it is a direct
service-to-service contract between Trax IO's compute and eMRO's Oracle-backed
Java application.

What's real: `fake_emro`, a FastAPI reference implementation backed by an
`InMemoryWritebackTarget`, built specifically so the Trax-side `Audited
WritebackTarget` Protocol (history ledger, 90-day rollback, shadow mode) can be
proven correct via contract tests without depending on eMRO's real
implementation ever existing yet (ADR-0003). This de-risks the *logic*; it does
not touch the *infrastructure* question.

\newpage

# 9. Infrastructure-as-Code Status Summary

| Stack / Package | Tests | `cdk synth` | `cdk deploy` | What's blocking deploy |
|---|---|---|---|---|
| `infra/feature-store` (`FeatureStoreStack`) | 11 passed | Clean | Not attempted — explicitly disabled in `app.py` | No deploy pipeline (Phase 8); no AWS account/credentials in any environment this has run in |
| `infra/observability-soc2` (`ObservabilitySoc2Stack`) | 40 passed | Clean | Not attempted | Same as above, plus: real SOC 2 framework ID, dedicated audit account not yet split out |
| Agent/compute plane (Bedrock, Strands, SageMaker) | — | No stack exists | — | Not started |
| Event plane (EventBridge, Kinesis, Lambda, Step Functions) | — | No stack exists | — | Not started |

\newpage

# 10. What It Actually Takes to Deploy

In rough dependency order:

1. **A real AWS account and credentials.** Every environment this project has
   run in so far has none — this is the hard blocker on anything past `cdk
   synth`.
2. **The Phase 8 per-tenant deploy pipeline** (OIDC-based, per the `app.py`
   comment) — CDK Pipelines or an equivalent CI/CD path into the `TraxAi`
   account. Today, running `cdk deploy` by hand against either package is
   explicitly discouraged by the code's own comments, not merely undocumented.
3. **Real SOC 2 framework ID discovery** (`aws auditmanager
   list-assessment-frameworks`) to replace the placeholder UUID.
4. **Split the audit bucket into a dedicated cross-account** per the design's
   dual-book pattern, before any real customer data reaches it.
5. **Wire the excluded event-driven pieces** into `infra/feature-store` (Glue
   triggers/streaming, Kinesis, EventBridge) once the Event Plane has actual
   design-to-code work behind it (§7).
6. **Build the compute/agent plane from nothing** — this is the single largest
   remaining gap. Nothing in §6 has a line of Bedrock, Strands, or SageMaker
   code today; the local `SupervisorOrchestrator` and Python forecasting stack
   prove the logic but do not migrate themselves.
7. **A lighthouse tenant's onboarding runbook** (sub-project #10) to actually
   exercise per-tenant provisioning end-to-end once the above exists.

None of this is a criticism of sequencing — building the deterministic logic and
both UIs first, in a form that's fully testable without any cloud dependency, is
a reasonable and already-paying-off order of operations. This section exists so
that status is never ambiguous to whoever reads it next.

\newpage

# 11. Cost & Observability Model (Target)

Per the design, per-tenant cost is a first-class metric tagged on every LLM
invocation and SageMaker inference call, feeding anomaly alerts and commercial
pricing tiers. This has no implementation yet — there is no LLM invocation or
SageMaker call anywhere in the current codebase to tag. It remains accurately
described as target-state in the Architecture Guide; nothing in this guide
changes that.

# Appendix — Running the Real CDK Code Today

Both packages can be synthesized (not deployed) locally:

```bash
cd infra/feature-store && uv run --extra dev pytest        # 11 tests
cd infra/feature-store && uv run cdk synth                  # emits CloudFormation, no AWS call

cd infra/observability-soc2 && uv run --group dev pytest    # 40 tests
cd infra/observability-soc2 && uv run cdk synth
```

Both are safe to run with no AWS credentials configured — `cdk synth` only
renders templates locally; it does not call any AWS API.
