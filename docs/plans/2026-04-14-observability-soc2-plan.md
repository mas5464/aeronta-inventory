# Sub-plan #9 — Observability + SOC 2 Control Plane Implementation Plan

**Goal:** Ship the cross-cutting observability and SOC 2 Type II control plane that every other sub-plan hooks into. Operational observability answers "is the agent running?"; business observability answers "is it correct?"; the SOC 2 control plane answers "can we prove both to an auditor without a scramble?" All three must work from Wave 0 day one — retroactive SOC 2 evidence is impossible.

**Owner:** Platform engineering (observability) + SecOps (SOC 2).

**Tech Stack:** OpenTelemetry Collector + AWS Distro for OpenTelemetry (ADOT), AWS X-Ray, Managed Grafana, CloudWatch Logs + Metrics, OpenSearch, CloudTrail Lake, AWS Audit Manager, AWS Config, AWS Security Hub, AWS KMS, AWS Secrets Manager, GuardDuty.

---

## What this plan owns

Three distinct but overlapping observability surfaces, plus the SOC 2 control plane:

1. **Operational observability** — traces, metrics, logs for every component in every sub-plan.
2. **Business observability** — evaluation pipeline output, planner engagement, savings attribution feeding the BVR (sub-plan #8).
3. **SOC 2 evidence pipeline** — continuous control testing, immutable audit logs, annual attestation readiness.
4. **Incident response infrastructure** — runbooks, kill switches, paging.

---

## Phases

### Phase 0: OTel standard + SDK bootstrap (2 weeks)

- Standard: OpenTelemetry semantic conventions + custom `trax_io.*` attributes (`trax_io.tenant_id`, `trax_io.specialist`, `trax_io.regime`, `trax_io.policy_kind`, `trax_io.idempotency_key`, `trax_io.model_id`, `trax_io.model_version`, `trax_io.provenance_id`).
- Shared Python package `trax-io-otel` imported by every Python sub-plan; shared Java wrapper for eMRO-side sub-plans.
- Every span tagged with tenant, specialist, request ID.
- OTel Collector deployed as a DaemonSet (where applicable) and as a sidecar in AgentCore Runtime.

### Phase 1: X-Ray + CloudWatch (2 weeks)

- X-Ray as the primary trace backend; retention 30 days hot, 1 year cold in S3.
- CloudWatch Logs per specialist per tenant log group; OpenSearch ingestion for search.
- CloudWatch Metrics with per-tenant cost attribution, per-specialist latency, error rate.
- Managed Grafana workspace with per-tenant dashboards and a global Trax Ops dashboard.

### Phase 2: SLOs + burn-rate alerting (3 weeks)

- SLOs codified per design §7.1:
 - Nightly recompute within 6 hours.
 - Event-lane decision within 15 minutes p95.
 - Writeback success rate ≥ 99.9%.
 - Per-tenant cost within $X/month (tenant-configurable).
- Multi-window burn-rate alerts (Google SRE style).
- PagerDuty integration; per-tenant escalation paths.

### Phase 3: Per-tenant cost attribution (2 weeks)

- Every LLM invocation tagged with tenant, specialist, session ID, token counts (input/output/cached).
- Every SageMaker endpoint invocation tagged similarly.
- Nightly roll-up into a `cost_ledger` Iceberg table (tenant_id, date, component, tokens, $ attributed).
- Surfaced in Planner UI (sub-plan #7) and in Trax Ops dashboard.
- Anomaly detection on per-tenant cost; pages on 2× normal-day spikes.

### Phase 4: Business observability — evaluation pipeline connector (2 weeks)

- Sub-plan #5's evaluation pipeline emits metrics to this plan's CloudWatch + Iceberg.
- Planner engagement metrics (queue review time, override rate, bulk-approve rate) collected from Planner UI BFF (sub-plan #7).
- Daily per-tenant scoreboard feeds BVR pipeline (sub-plan #8).

### Phase 5: CloudTrail Lake event store (2 weeks)

- CloudTrail Lake event data store per organization with 7-year retention.
- All IAM + KMS + AgentCore Identity events captured.
- All Writeback REST API calls (sub-plan #6) captured via custom trail.
- Saved queries for common audit asks ("every `PN_INVENTORY_LEVEL` write for tenant X in March").

### Phase 6: Immutable audit S3 mirror (1 week)

- Per-tenant audit buckets with S3 Object Lock, Compliance mode, 7-year retention.
- Every writeback history row (sub-plan #6 `PN_INVENTORY_LEVEL_HISTORY`) mirrored to S3 within 60 seconds.
- Every evaluation result mirrored.
- Every approval task state change mirrored.
- SSE-KMS with per-tenant CMK.
- Cross-region replication for DR.

### Phase 7: AWS Audit Manager SOC 2 framework (4 weeks)

- Attach the built-in SOC 2 framework to the organization account.
- Customize evidence collectors for the Trax IO controls beyond the baseline:
 - Tenant-isolation: automated check that every IAM policy with tenant scope uses correct condition keys.
 - KMS rotation: annual rotation verified.
 - Model lineage: every production model has Model Registry lineage.
 - Approval chain: every Writeback has either planner approval or Cedar-allowed autonomous tier.
- Evidence reviews automated; gaps flagged to SecOps weekly.

### Phase 8: AWS Config + Security Hub quarterly control tests (3 weeks)

- Config rules encoding SOC 2 control expectations: encryption at rest, block-public-access, MFA on root, etc.
- Security Hub aggregates findings; CIS AWS Foundations Benchmark enabled.
- Quarterly control tests automated; results feed Audit Manager evidence.

### Phase 9: Incident response infrastructure (3 weeks)

- Runbooks per design §7.6:
 - Model-produced garbage.
 - Writeback failure.
 - Event-lane stall.
 - Tenant data leakage.
 - LLM prompt injection via free-text eMRO fields.
- Each runbook includes detection signals, immediate actions, escalation path, post-incident review template.
- Global kill switch (pauses all tenant Supervisors within 5 minutes) + per-tenant kill switch (60 seconds).
- 30-day post-incident action SLA.

### Phase 10: SOC 2 Type II audit engagement (10 weeks)

- Engage SOC 2 auditor (Big 4 or specialized firm; selection by Trax SecOps).
- Scope: Trust Services Criteria — Security, Availability, Confidentiality, Processing Integrity (Privacy deferred to v2 when EU customers are onboarded).
- Operating effectiveness window: minimum 3 months; target 6 months before first attestation.
- Evidence review: automated via Audit Manager + manual walkthroughs.
- Gap remediation if any.
- First attestation target: before lighthouse customer exits shadow mode.

### Phase 11: GuardDuty + threat detection (2 weeks)

- GuardDuty enabled with tenant-aware finding filters.
- Unusual IAM activity (cross-tenant access attempts) auto-pages SecOps.
- Kubernetes & EKS protection enabled where applicable.

---

## Cross-cutting deliverables

- Every sub-plan's CI gate includes "observability lint" — verifies every agent emits expected tenant-tagged spans, cost records, and audit events.
- New-engineer onboarding doc: "SOC 2 for engineers" — what to do and what to avoid per control.
- Audit-reviewer dashboard in Grafana: single-pane-of-glass view for the SOC 2 auditor.
- PII redaction library (`trax-io-redact`) — used by Agent Spine on free-text eMRO fields before they ever enter LLM prompts.

## Key risks

- **Retroactive SOC 2 impossible.** Starting this from Wave 0 day one is non-negotiable.
- **Evidence collection drift.** Audit Manager automation must stay current; quarterly evidence review by SecOps.
- **Tenant isolation regression.** Most dangerous silent failure. Mitigation: synthetic cross-tenant access attempts in CI; any success fails the build.

## Estimated timeline

- Phases 0–6 parallel with Wave 0/1 of other sub-plans: ~14 weeks elapsed.
- Phase 7: starts month 4.
- Phase 10: 10-week audit engagement, kick off month 4, attestation by month 6.

**Team:** 1 platform observability lead + 1 SRE + 1 SecOps engineer + external SOC 2 auditor.
