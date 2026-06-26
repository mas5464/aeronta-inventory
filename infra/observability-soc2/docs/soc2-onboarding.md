# SOC 2 Onboarding for Trax IO Engineers

**Audience:** Every engineer working on Trax IO sub-plans #1–#10.
**Status:** Mandatory reading before your first PR touches any AWS resource, tenant data, or log emission.
**Owner:** Platform + SecOps (sub-project #9).

SOC 2 Type II evidence starts accruing **Day One of Wave 0**. Retroactive evidence is impossible. This document is your contract with the control plane — do these things, and your sub-project's evidence shows up in Audit Manager automatically. Skip them, and you block the attestation.

Keep this doc open on your second monitor while you work.

---

## 1. CloudTrail tagging conventions

Every AWS resource you create (CDK, Terraform, console, CLI — no exceptions) **must** carry these tags:

| Tag key      | Value                                  | Notes                                                   |
| ------------ | -------------------------------------- | ------------------------------------------------------- |
| `Project`    | `TraxIO`                               | Literal string. All resources.                          |
| `Owner`      | `Platform+SecOps`, `Data`, `Agents`, … | Your sub-team.                                          |
| `Compliance` | `SOC2-TypeII`                          | Literal string. All resources.                          |
| `TenantId`   | `<tenant_id>` or `shared`              | Required on tenant-scoped resources (buckets, KMS, etc).|
| `SubPlan`    | `01`–`10`                              | Two-digit sub-plan number for traceability.             |

CDK shortcut:

```python
cdk.Tags.of(stack).add("Project", "TraxIO")
cdk.Tags.of(stack).add("Owner", "Data")
cdk.Tags.of(stack).add("Compliance", "SOC2-TypeII")
cdk.Tags.of(stack).add("SubPlan", "02")
```

Per-resource tenant tag:

```python
cdk.Tags.of(tenant_bucket).add("TenantId", tenant_id)
```

CI will fail any stack that synthesizes a resource without `Project`, `Owner`, `Compliance` (lint rule ships in Phase 2 of sub-plan #9).

---

## 2. KMS envelope encryption patterns

**Rule:** Tenant data is encrypted with the tenant's CMK. Never with an AWS-managed key. Never with a shared CMK.

### Discover the tenant key

```python
from aws_cdk import aws_kms as kms

tenant_key = kms.Alias.from_alias_name(
    scope, f"TenantKey-{tenant_id}",
    alias_name=f"alias/trax-io/tenant/{tenant_id}",
)
```

### Apply to the resources you create

* **S3 buckets holding tenant data** → `encryption=BucketEncryption.KMS`, `encryption_key=tenant_key`.
* **DynamoDB tables with tenant data** → `encryption=TableEncryption.CUSTOMER_MANAGED`, `encryption_key=tenant_key`.
* **CloudWatch log groups for tenant activity** → `encryption_key=tenant_key`, name `/trax-io/<tenant_id>/<component>`.
* **SQS/SNS with tenant payloads** → KMS master key = tenant key.
* **Secrets Manager secrets** → Use per-tenant keys for tenant-scoped secrets.

### Application-level envelope encryption

If you're storing tenant data in a store you don't fully control (e.g., Iceberg tables where partitions are rewritten by Glue), do application-level envelope encryption:

1. `GenerateDataKey` against the tenant alias.
2. Encrypt payload with the plaintext data key.
3. Persist `{ciphertext, encrypted_data_key, key_id}`.
4. Zero the plaintext data key.

A shared helper `trax_io_crypto.envelope` arrives in Phase 2 of sub-plan #9.

### What you must NEVER do

* Decrypt one tenant's data in a context that also has access to another tenant's data. Tenant isolation is the control most likely to silently regress.
* Use SSE-S3 (`AES256`) on any bucket containing tenant data.
* Disable key rotation on a CMK. Audit Manager tests this quarterly.

---

## 3. Audit-log emission requirements

Every **business-meaningful** action in your sub-project must emit an audit event. "Business-meaningful" means anything an auditor or a tenant admin might ever want to reconstruct: writebacks, approvals, model promotions, extract-run completions, kill-switch activations, Cedar policy changes, etc.

### Where to emit

* **Synchronous audit log** — CloudWatch Logs, log group `/trax-io/<tenant_id>/<component>`.
* **Immutable mirror** — S3 Object Lock Compliance bucket, prefix `audit/<sub_plan>/<YYYY>/<MM>/<DD>/`. The shared `trax-io-audit-mirror` Lambda (Phase 6) handles the mirror; you only write to CloudWatch.

<a id="audit-log-schema"></a>
### Required event schema

Every audit event MUST carry the following top-level fields. Events missing
any of these are rejected by the audit-mirror Lambda and do not count toward
SOC 2 evidence:

* `tenant_id` — tenant slug the event is scoped to.
* `actor` — who did it (user, agent, or system), as a structured object.
* `action` — dotted `verb.noun` string, e.g., `PN_INVENTORY_LEVEL.write`.
* `resource` — what was acted on (kind + id, with optional version fields).
* `at` — event timestamp as an RFC 3339 UTC string.
* `trace_id` — OTel trace ID, for linking to X-Ray.
* `schema_version` — literal `"1.0.0"` for Phase 2; bumped on breaking changes.

Fields such as `event_id`, `sub_plan`, `component`, `outcome`, `reason`,
`provenance_id`, `idempotency_key`, and `request_id` remain recommended but
are enforced per-sub-plan rather than by the schema gate. The canonical
shape below is what the audit-mirror and Audit Manager evidence collectors
consume.

```jsonc
{
  "schema_version": "1.0.0",
  "tenant_id": "lighthouse-alpha",
  "at": "2026-04-16T12:34:56Z",
  "actor": {
    "kind": "user",                      // "user" | "agent" | "system"
    "id": "planner:jane.doe",
    "session_id": "sess-7f3c…"
  },
  "action": "PN_INVENTORY_LEVEL.write",
  "resource": {
    "kind": "inventory_level",
    "id": "PN-12345@MIA",
    "version_before": "2026-04-15T09:00:00Z",
    "version_after": "2026-04-16T12:34:56Z"
  },
  "trace_id": "1-5f2b-…",
  "event_id": "uuid-v7",
  "sub_plan": "06",
  "component": "writeback-rest",
  "outcome": "success",
  "reason": "policy:tier-B-auto-approve",
  "provenance_id": "prov-9a1…",
  "idempotency_key": "wb-2026-04-16-…",
  "request_id": "req-…"
}
```

### OTel span requirements

Every span your code produces must carry:

* `trax_io.tenant_id`
* `trax_io.specialist` (if applicable — e.g., `forecasting`, `policy`, `supervisor`)
* `trax_io.regime` (if applicable)
* `trax_io.policy_kind` (if applicable)
* `trax_io.idempotency_key` (on write actions)
* `trax_io.model_id` + `trax_io.model_version` (on model-inference spans)
* `trax_io.provenance_id` (on model-driven actions)

The `trax-io-otel` shared Python package (and its Java sibling for eMRO-side code) wraps these. Import it; don't roll your own OTel setup.

### What you must NEVER do

* Log raw PII, customer passwords, access tokens, or free-text eMRO fields. Run all free-text through `trax-io-redact` (shipped by sub-plan #9 cross-cutting deliverables).
* Emit an audit event without `tenant_id` and `provenance_id` populated (where applicable).
* Use `print()` or `logging.info()` to stdout in production code paths. Always go through `trax-io-otel`.

---

## 4. Pre-PR checklist

Before you open a PR against any sub-plan, verify:

- [ ] Every AWS resource in your CDK synth has `Project`, `Owner`, `Compliance`, `SubPlan` tags; tenant-scoped resources also have `TenantId`.
- [ ] Every CMK you touched has `enable_key_rotation=True`.
- [ ] No tenant data resource uses SSE-S3 or an AWS-managed key.
- [ ] Every new write path emits an audit event conforming to the schema above.
- [ ] Every new span includes the required `trax_io.*` attributes.
- [ ] No free-text eMRO field reaches a log line, a prompt, or an S3 object without passing through `trax-io-redact`.
- [ ] Your IAM policies include tenant-scoping condition keys (`aws:ResourceTag/TenantId` or principal-tag equivalents) where tenant scope is relevant.

CI will enforce most of these via the "observability lint" gate landing in Phase 2 of sub-plan #9. Do not wait for CI — get them right on the first pass.

---

## 5. Questions / escalations

* Plan + design: see links at the top of [the Phase 1 README](../README.md).
* Control-plane bugs or evidence gaps: page Platform + SecOps (sub-project #9).
* Policy questions about a specific control: SecOps owns the final call.

If you're unsure whether something counts as auditable — assume yes and emit the event. Excess audit noise is cheap; missing evidence is catastrophic.
