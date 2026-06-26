# Trax IO — Observability + SOC 2 Control Plane (Phase 1 Scaffold)

**Sub-project:** #9 (Wave 0, P0)
**Plan:** [`docs/plans/2026-04-14-observability-soc2-plan.md`](../../docs/plans/2026-04-14-observability-soc2-plan.md)
**Design reference:** [`docs/design/2026-04-14-trax-io-inventory-optimizer-design.md`](../../docs/design/2026-04-14-trax-io-inventory-optimizer-design.md) §4.5, §7
**Status:** Phase 1 — `cdk synth` only. No deploy.

> **Mandatory reading for every Trax IO engineer** before opening a PR against any other sub-plan. See [`docs/soc2-onboarding.md`](docs/soc2-onboarding.md).

---

## Why SOC 2 evidence starts Day One

SOC 2 Type II is an **operating-effectiveness** attestation. The auditor inspects whether controls worked continuously across a 3–6 month observation window. **Evidence cannot be created retroactively.** A CloudTrail event that was never captured on 2026-04-16 cannot be conjured up on 2026-10-01 when the auditor asks for it. The first attestation targets month 6 of the build; therefore every CloudTrail event, every KMS key rotation, every audit-log row from today forward counts.

This is why this sub-project is Wave 0 P0 alongside the data plane — not after it.

---

## What Phase 1 provisions

All resources synth in a single CDK stack (`TraxIoObservabilitySoc2Stack`) and carry tags `Project=TraxIO`, `Owner=Platform+SecOps`, `Compliance=SOC2-TypeII`.

| Resource                              | Notes                                                                              |
| ------------------------------------- | ---------------------------------------------------------------------------------- |
| CloudTrail Lake event data store      | 7-year (2557-day) retention, multi-region, termination protection on, KMS-encrypted |
| AWS Audit Manager SOC 2 assessment    | Built-in SOC 2 framework (L1 `CfnAssessment` — see deviations below)                |
| Per-tenant KMS CMKs                   | One per tenant, alias `alias/trax-io/tenant/<tenant_id>`, annual rotation           |
| Org-wide audit KMS CMK                | `alias/trax-io/audit`, annual rotation, encrypts audit bucket + CloudTrail Lake     |
| Audit S3 bucket                       | Object Lock **Compliance** mode, 7-year retention, KMS, public access blocked       |
| OTel Collector Fargate placeholder    | VPC + ECS cluster + task def; X-Ray + CloudWatch IAM already attached               |
| Per-tenant CloudWatch log groups      | `/trax-io/<tenant_id>`, encrypted with the tenant's CMK                             |

---

## Per-tenant KMS provisioning flow

1. When a new tenant is onboarded (see [`docs/plans/2026-04-14-tenant-onboarding-runbook.md`](../../docs/plans/2026-04-14-tenant-onboarding-runbook.md)), the tenant ID is appended to the `trax_io:tenants` CDK context array.
2. The stack's `_make_tenant_kms_key(tenant_id)` construct helper provisions:
   * A KMS CMK with `enable_key_rotation=True` and a 365-day rotation period.
   * An alias `alias/trax-io/tenant/<tenant_id>` — this alias is the **contract** that every downstream sub-project must use to discover the tenant's envelope key.
   * A `RETAIN` removal policy — tenant keys MUST survive stack deletes.
3. A matching CloudWatch log group `/trax-io/<tenant_id>` is encrypted with that CMK.
4. Downstream sub-projects (Feature Store, Event Publisher, Writeback REST, etc.) perform envelope encryption using this alias. **Never** encrypt tenant data with AWS-managed keys.

---

## Dual-book audit pattern

Every auditable event — a writeback `PN_INVENTORY_LEVEL_HISTORY` row, an evaluation result, an approval task state change — is written **twice**:

1. **In-place (eMRO history tables, DynamoDB streams, application databases).** This is what the eMRO operator sees and what the application uses for its own audit UI.
2. **Trax audit account mirror (S3 Object Lock Compliance + CloudTrail Lake).** This is what the SOC 2 auditor inspects. It is in a **separate AWS account** from the workload account, with a distinct principal set and no humans granted `PutObject` permission — only the mirroring Lambda.

**Phase 1 caveat:** The mirror bucket is synthesized in the same stack/account as the workload for convenience. Phase 6 of the plan moves it to the dedicated Trax audit account. The CFN resource has metadata flagging this split.

Why two books? If the eMRO-side history is tampered with (or compromised by a tenant-admin credential), the Compliance-mode Object-Lock mirror is non-bypassable and forensically intact. Compliance mode means *no one — not even the AWS root account — can shorten the retention or delete an object before the retention clock expires.*

---

## Onboarding for engineers in other sub-projects

If you are building in any of sub-plans #1–#8 or #10, you MUST read [`docs/soc2-onboarding.md`](docs/soc2-onboarding.md) before your first PR. It is short.

Quick summary of your obligations:

* **Tag every resource** with `Project=TraxIO`, `Owner=<your sub-team>`, `Compliance=SOC2-TypeII`, and `TenantId=<tenant_id>` where applicable.
* **Use the per-tenant KMS alias** `alias/trax-io/tenant/<tenant_id>` for envelope encryption. Do not create your own CMKs for tenant data.
* **Emit audit log events** to the conventional CloudWatch log group `/trax-io/<tenant_id>` using the shared `trax-io-otel` span + `trax-io-audit` emission libraries (arriving in Phase 2 of this sub-plan).
* **Never log PII or free-text eMRO fields** without running them through `trax-io-redact`.
* **CloudTrail is on.** Every IAM, KMS, and S3 API call your service makes is captured. Design accordingly.

---

## Verify

```bash
cd infra/observability-soc2
uv sync
uv run cdk synth                    # emits CloudFormation to cdk.out/
uv run pytest                        # runs synth-assertion tests
uv run ruff check .
```

(You need the AWS CDK v2 CLI installed globally: `npm install -g aws-cdk`.)

---

## Deviations from the plan doc

1. **Audit Manager — no L2 in `aws-cdk-lib`.** The plan says "attach the built-in SOC 2 framework." There is no CDK L2 construct for Audit Manager at this time, so we use the L1 `CfnAssessment`. The real framework UUID must be resolved at deploy time via `aws auditmanager list-assessment-frameworks` and passed via CDK context (`trax_io:soc2_framework_id`); a placeholder is used for synth. This is noted inline in `stack.py`.
2. **Audit bucket in workload account (Phase 1 only).** The plan specifies a dedicated Trax audit AWS account. Phase 1 keeps the bucket in-stack to keep synth standalone; Phase 6 moves it. Flagged via `CfnBucket.add_metadata("trax_io:account_split", ...)`.
3. **Audit Manager framework ID is a placeholder.** Synth does not contact AWS, so we cannot resolve the real framework UUID. `test_audit_manager_soc2_assessment_is_attached` asserts the resource exists and is `ACTIVE`; framework-ID validity is deferred to Phase 7.
4. **OTel Collector routing not yet wired.** Phase 1 provisions only the VPC, cluster, task role, and task def with the public ECR AWS OTel collector image. Actual collector config (X-Ray + CloudWatch + OpenSearch routing) lands in Phase 2 of this sub-plan.

None of these deviations affect the hard constraints: Object Lock Compliance, KMS rotation, or 7-year CloudTrail Lake retention.
