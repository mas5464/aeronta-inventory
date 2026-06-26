---
title: "Trax IO — Integration Handoff Guide"
subtitle: "For the eMRO product team and customer DBA reviewers"
author: "Miguel Sosa, VP Head of Innovation · Trax"
date: "2026-04-14"
---

\newpage

# 1. Purpose and Audience

This guide is written for three audiences who need the same information for slightly different reasons:

1. **eMRO product engineers** who will implement sub-plans #3 (Outbound Event Publisher), #6 (Writeback REST API), and #7 (Planner UI) inside the eMRO codebase.
2. **Customer DBAs and IT security reviewers** who need to understand what Trax IO does inside their environment, what data leaves, and how it is secured.
3. **Customer planning leads and CIOs** who approve the architecture before the lighthouse pilot starts.

The guide summarizes the four integration contracts between Trax IO and eMRO, the data that crosses the boundary, the security model, and the operational expectations. For full implementation detail, see the per-sub-plan documents; for commercial context, see the Steering Committee one-pager.

\newpage

# 2. What Trax IO Is

Trax IO is a multi-tenant AI inventory optimization agent that layers on top of the eMRO product. It continuously recomputes `(ROP, EOQ, Safety Stock, Max)` per `PN × Location` under tiered autonomy, reacts in real time to flight-plan changes and AD/SB issuance, and writes recommendations back into eMRO under full audit and rollback. It is hosted in Trax's AWS account; customer data is tenant-isolated throughout.

The product does not replace eMRO's planning or procurement surfaces. Trax IO recommends; eMRO records and executes. The only table Trax IO writes to is `PN_INVENTORY_LEVEL`, via a scoped REST API the eMRO product team implements in sub-plan #6.

\newpage

# 3. The Four Integration Contracts

Four distinct integration surfaces sit between Trax IO and eMRO. Each is contract-first; each has a dedicated owning document; each ships with a shared test suite enforced in both repositories.

## 3.1 Nightly Extract (sub-plan #1)

A Trax-signed Python CLI utility (`trax-io-extract`), installed by the customer DBA inside the customer environment. Runs nightly on the customer's existing scheduler (cron, Windows Task Scheduler, Control-M). Queries eMRO's Oracle database via the 12 v1 SQLs, writes Parquet + a SHA-256 manifest locally, requests a presigned URL from Trax IO's upload endpoint, and PUTs the files to Trax's S3 landing bucket over HTTPS with mutual TLS. No customer AWS credentials are required.

**Security properties.** The utility binary is Ed25519-signed by Trax's KMS-held signing key; the binary verifies its own signature on every startup. The per-customer client certificate used for mTLS is issued by Trax's private CA and stored in a customer-controlled secret store. The utility keeps a signed append-only audit log of every query executed and every file uploaded, rotated at 100 MB, retained for 7 years.

**What leaves the environment.** Only the Parquet extracts covering the 12 v1 SQL result sets. No free-text fields containing PII are expected; `REMOVAL_REASON` and similar may contain incidental mechanic names and are scrubbed through a configurable redaction policy before entering any Trax IO observability index (the audit bucket retains the originals for customer-side audit).

## 3.2 Outbound Event Publisher (sub-plan #3)

A Spring Boot add-on module that ships inside a scheduled eMRO release. Database triggers on six source tables populate a `TRAX_EVENT_OUTBOX` table; a scheduled drainer reads the outbox and pushes seven domain event kinds over HTTPS with mTLS to the Trax IO event endpoint:

- `flight_completed`
- `stock_moved`
- `wo_scheduled`
- `vendor_price_changed`
- `plan_published`
- `removal_recorded`
- `eo_published` (including airworthiness directives and service bulletins)

**Delivery guarantees.** At-least-once with idempotency-key deduplication; events delivered in `produced_at` order per `(tenant_id, kind)`; retry with exponential backoff up to 7 attempts, then dead-letter to a local persistent queue with an operator UI for replay.

**What leaves the environment.** JSON payloads matching the frozen schemas published at `docs/contracts/schemas/`. Typical volumes: thousands of `stock_moved` per day, hundreds of `wo_scheduled` per day, tens of `flight_completed` per tail per month.

Full contract at `docs/contracts/2026-04-14-emro-event-publisher-contract.md`.

## 3.3 Writeback REST API (sub-plan #6)

A new REST surface inside eMRO that the Trax IO Writeback Agent calls to update `PN_INVENTORY_LEVEL`. Four endpoints:

- `PUT /v1/tenants/{tenant_id}/inventory-level/{pn}/{location}` — transactional update.
- `GET /v1/tenants/{tenant_id}/inventory-level/{pn}/{location}/history` — paginated history.
- `POST /v1/tenants/{tenant_id}/inventory-level/{pn}/{location}/rollback` — revert to prior version (requires planner principal).
- `POST /v1/tenants/{tenant_id}/inventory-level/bulk-rollback` — filtered revert (requires planner principal + confirmation token).

**Auth.** Mutual TLS (Trax service-principal cert) + `X-Service-Principal: trax-io` header + short-lived bearer token JWT issued by Trax IO's token issuer service. All three must agree; any mismatch returns 403.

**Audit.** Every write creates a `PN_INVENTORY_LEVEL_HISTORY` row inside the customer's Oracle database with `{old_values, new_values, provenance_id, changed_by_agent, agent_version, tier, idempotency_key, changed_at}`. This history table is the rollback surface and the SOC 2 audit trail.

**Idempotency.** Every call carries an `Idempotency-Key` header. The same key + same body returns the original response verbatim for 30 days. Same key + different body returns 409.

**Rate limiting.** Default 100 writes/sec per tenant, configurable.

**Rollback window.** 90 days default, configurable, cannot be set to zero.

## 3.4 Planner UI "Trax IO Review" (sub-plan #7)

An embedded eMRO module where the customer's planning lead reviews Trax IO recommendations. Runs as an iframe or native eMRO module hosted in the eMRO web application. Calls a Trax-hosted Backend-for-Frontend (`trax-io-planner-bff`) for recommendation data; calls the Writeback REST API (section 3.3) for approvals and rollbacks.

**Surfaces.** Queue tab (pending recommendations sorted by priority), History tab, Bulk Actions tab (filter builder), Weekly Digest tab (Tier-C auto-applied writes), Reports tab (monthly Business Value Report PDF), Settings tab (service-level targets, autonomy bands), Kill Switch (one-toggle tenant pause).

**Localization.** English, French, Spanish, Portuguese at launch; German and Japanese in v1.1.

**Accessibility.** WCAG 2.1 AA. Keyboard navigation throughout; screen-reader friendly; high-contrast mode.

\newpage

# 4. Agent Orchestration Inside Trax IO

For engineering review and security assessment, it is helpful to see what Trax IO does with the data it receives. The diagram below shows the full agent topology — the Supervisor and six specialists that collaborate to produce every recommendation, plus the shared AgentCore Memory / Identity / Gateway / Observability infrastructure that enforces tenant isolation.

![Agent Orchestration Topology](../diagrams/png/01-agent-orchestration.png)

The agents on the right (dashed borders) are phases 2–6 roadmap — not present in v1 but architected for. Every additional phase adds one specialist; none require touching the Supervisor's contract.

The Writeback Agent is the only component with IAM permission to call the Writeback REST API in section 3.3. All other agents are read-only toward customer data. This blast-radius minimization is deliberate.

\newpage

# 5. End-to-End Data Flow

The diagram below traces every byte from the customer's Oracle database to the recommendation written back into `PN_INVENTORY_LEVEL`. It is the most useful single reference for security review.

![Trax IO End-to-End Data Flow](../diagrams/png/02-data-flow.png)

Three parallel ingestion paths (nightly extract, event lane, optional DMS/CDC) feed a tenant-isolated Iceberg + DynamoDB feature store. Agent decisions flow back through either the approval queue (Tier A) or the Writeback REST API directly (Tier B/C). Every write and every decision is dual-booked to the customer's eMRO database and to a Trax-side audit S3 bucket with S3 Object Lock Compliance mode and 7-year retention.

\newpage

# 6. Security Model

Trax IO's security model rests on five pillars:

**Tenant isolation at four layers.** Contract layer (every request carries `tenant_id`; every agent enforces `current_tenant()` matches), agent layer (AgentCore Memory namespaces, Cedar policies with tenant as principal attribute), data layer (per-tenant KMS CMKs, per-tenant Iceberg partitions, per-tenant DynamoDB tables), and infrastructure layer (per-tenant CDK stacks, IAM boundaries, CloudTrail scopes). Synthetic cross-tenant access attempts run in every CI build; any success fails the build.

**Mutual TLS everywhere at the boundary.** Nightly Extract Utility → Trax IO, Event Publisher → Trax IO, Writeback Agent → eMRO Writeback REST. All three directions use mTLS with per-tenant client certificates issued by Trax's private CA. Certificates expire every 13 months and are rotated via a published runbook.

**Encryption at rest.** Every S3 bucket, every Iceberg table, every DynamoDB table, and every Glue-derived artifact is encrypted with a per-tenant KMS CMK. Keys are rotated annually. Customer has visibility into key-usage audit logs via AWS CloudTrail.

**Audit trail dual-booked.** Every recommendation, every write, and every approval is logged to both (a) the customer's eMRO `PN_INVENTORY_LEVEL_HISTORY` table and (b) an immutable Trax-side S3 audit bucket with Object Lock Compliance mode and 7-year retention. Neither party can unilaterally rewrite history.

**SOC 2 Type II first attestation** before the lighthouse customer exits shadow mode. AWS Audit Manager runs a pre-built SOC 2 framework continuously; evidence is collected automatically; quarterly control tests are automated via AWS Config and Security Hub.

**Prompt injection defense.** Free-text eMRO fields (`REMOVAL_REASON`, `DemandNote`, engineering order `title`) are mechanic- and regulator-authored and treated as untrusted. They are scrubbed and sandboxed before ever reaching a language model prompt. The v1 red-team evaluation suite includes prompt-injection payloads as regression tests.

**Financial and identity data never leaves the customer environment.** The 12 v1 SQLs do not include PII; the event schemas do not include PII; free-text fields that might carry incidental PII are redacted at ingestion. No customer credit-card data, SSN-equivalent data, or customer-employee PII enters Trax IO. The extract utility runs with a read-only Oracle role restricted to the 12 v1 SQLs; no write access to customer data is ever requested or granted.

\newpage

# 7. Event Lane

For the eMRO product team, the event lane is the most substantial integration surface to build. The diagram below shows the architecture end-to-end.

![Event Lane Architecture](../diagrams/png/04-event-lane.png)

Your side of the line (left side of the diagram, "eMRO (Customer Environment)") is the Spring Boot add-on: DB triggers, outbox table, drainer, DLQ, operator UI. Our side (right side) is the API Gateway + Lambda + Kinesis + three parallel consumers.

The contract between sides is frozen in `docs/contracts/2026-04-14-emro-event-publisher-contract.md`. Your implementation must pass the shared contract test suite (a pip-installable Python test package that runs against both `fake_event_endpoint` in the Trax IO repo and your real eMRO endpoint in staging). Any divergence fails CI in both repos.

\newpage

# 8. Recommendation Pipeline

For security review and operational due diligence, this diagram shows what happens to every recommendation from the moment the Supervisor is invoked to the moment `PN_INVENTORY_LEVEL` is written.

![Recommendation Pipeline](../diagrams/png/05-recommendation-pipeline.png)

Six steps, deterministic, auditable. The Guardrail & Approval Agent is the central governance point and never-bypassed. The Writeback Agent is the only component with mutation rights. Every step is logged with a cryptographically-chainable `provenance_id` that flows from the forecast into the policy into the writeback audit record. Given any historical recommendation, the exact inputs the model saw are reconstructible from Iceberg time-travel.

\newpage

# 9. Onboarding a New Customer

Trax IO is designed for repeatable onboarding rather than bespoke consulting engagements. Sub-plan #10 codifies the process; the diagram below is the lifecycle.

![Tenant Onboarding Lifecycle](../diagrams/png/06-onboarding-lifecycle.png)

From contract signature to first production Tier B/C write: ~20 weeks. Phase 5 Shadow Mode runs 30–90 days with zero writes to `PN_INVENTORY_LEVEL`; the agent produces recommendations, the customer's planning lead reviews them side-by-side with what Trax IO would have done. Exit from shadow requires weighted-MAPE target achievement, planner "would approve" rate ≥ 70%, zero tenant-isolation incidents, and explicit planner sign-off.

Phase 6 Canary opens the narrowest possible first slice of real writes: tier-5 consumables under $100, single station. 30 days clean before expanding. Phases 7–8 widen Tier C then Tier B through weekly gates. Any regression rolls back a phase rather than pushing forward — the product's rule is "we will not break the customer for schedule reasons."

Kill switches are first-class operational surfaces. Per-tenant kill switch reverts the agent to shadow mode within 60 seconds; global kill switch covers all tenants within 5 minutes.

\newpage

# 10. Operational Expectations

**SLOs published to customers:**
- Nightly recompute within 6 hours.
- Event-lane decision within 15 minutes p95.
- Writeback success rate ≥ 99.9%.
- Trax IO event endpoint: 99.9% availability per calendar month.

**Customer-visible controls:**
- Kill switch toggle in the Planner UI (per-tenant).
- Service-level target override per criticality tier.
- Autonomy band override (delta caps, cost caps).
- Per-event-kind feature flags in the Event Publisher operator UI.
- Rollback of any write within 90 days (configurable; minimum 90 days, cannot be zero).

**Customer-visible reporting:**
- Monthly Business Value Report PDF posted to the Planner UI.
- Weekly digest of Tier C auto-applied writes.
- 14-day planner notification window for every Tier B write.
- Real-time Tier-A approval queue.

**Joint on-call.** Contract-test failures (schema drift, rate limit exhaustion, certificate expiry) page both teams via shared PagerDuty services.

**Quarterly contract review.** Both teams review event volumes, schema usage, new field requests, and evolving consumer needs. Breaking changes require one full eMRO release cycle of deprecation window.

\newpage

# 11. Sign-off Checklist for the Lighthouse Customer

| Item | Reviewer | Status |
|---|---|---|
| Master Service Agreement including federated-learning opt-in clause | Customer Legal | |
| Security review package for Extract Utility (sub-plan #1) | Customer InfoSec | |
| Event Publisher contract sign-off (sub-plan #3) | Customer Architecture + Trax | |
| Writeback REST OpenAPI sign-off (sub-plan #6) | Customer Architecture + Trax | |
| SOC 2 Type II attestation in progress | Customer Compliance | |
| Essentiality mapping interview complete | Customer Planning Lead | |
| Service-level targets calibrated and documented | Customer Planning + CFO | |
| AOG cost constant agreed for phase-3 planning | Customer Ops + CFO | |
| Kill-switch ownership assigned | Customer Operations | |
| Monthly Business Value Report methodology signed off | Customer CFO | |
| Shadow-mode exit criteria agreed | Customer Planning Lead | |
| Designated on-call engineer on both sides | Customer IT + Trax | |

# 12. Contact & Escalation

Primary Trax IO contacts during pilot:

- **Product & commercial:** Miguel Sosa (VP Head of Innovation)
- **Platform on-call:** via Trax PagerDuty trax-io-platform
- **ML on-call:** via Trax PagerDuty trax-io-ml
- **SecOps on-call:** via Trax PagerDuty trax-io-secops
- **Customer success lead:** assigned per tenant at Phase 1 kickoff

Escalation path for customer-impacting incidents: customer on-call → Trax platform on-call → Trax IO incident commander → VP Innovation.
