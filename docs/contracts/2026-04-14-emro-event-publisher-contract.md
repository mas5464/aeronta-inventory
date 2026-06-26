# eMRO Outbound Event Publisher — Integration Contract

**Date:** 2026-04-14
**Owner:** Trax IO platform team + eMRO product team
**Status:** Draft v1 — for joint review and sign-off
**Implements:** Design §4.1 (event lane), Sub-plan #3 implementation plan

---

## Purpose

This document is the contract between eMRO (the producer) and Trax IO (the consumer) for the seven domain events that fire on every change in eMRO state relevant to inventory optimization. It defines transport, security, schema, ordering guarantees, retry semantics, and the operational expectations that bind both teams.

The contract is the single source of truth. The eMRO Java implementation (sub-plan #3) and the Trax IO consumer (sub-plan #4 Agent Spine) both build against this document. Schema files (JSON Schema) and an AsyncAPI 2.6 specification live alongside this document and are machine-checkable.

This contract is versioned with semver. Breaking changes require a major version bump, customer notification, and a deprecation window of one full eMRO release cycle.

---

## Transport

- **Protocol:** HTTPS with mTLS (TLS 1.3 minimum).
- **Direction:** eMRO → Trax IO event endpoint (push). No polling.
- **Endpoint URL pattern:** `https://events.trax-io.aws.trax.com/v1/tenants/{tenant_id}/events`
- **Method:** `POST`
- **Body:** Single event per request (v1). Batch endpoint (`POST /v1/tenants/{tenant_id}/events/batch`) reserved for v2.
- **Authentication:** Mutual TLS. The eMRO instance presents a per-tenant client certificate issued by Trax. Trax IO presents the events.trax-io endpoint certificate signed by AWS Private CA.
- **Authorization:** Cedar-evaluated. The TLS client certificate's CN identifies the tenant; events with `tenant_id` mismatched against the certificate are rejected with HTTP 403.
- **Compression:** `Content-Encoding: gzip` recommended for payloads > 1 KB.

---

## Event envelope (all events share this shape)

```json
{
  "event_id": "evt-018f7b2c-3d4e-5f6a-7b8c-9d0e1f2a3b4c",
  "tenant_id": "aircanada",
  "kind": "removal_recorded",
  "occurred_at": "2026-04-14T17:42:18.123Z",
  "schema_version": "1.0.0",
  "produced_at": "2026-04-14T17:42:18.557Z",
  "producer": {
    "system": "emro",
    "version": "12.4.2",
    "instance": "ac-prod-yyz"
  },
  "payload": {
    /* event-kind-specific schema, see below */
  }
}
```

**Required envelope fields:**

| Field | Type | Description |
|---|---|---|
| `event_id` | string (UUIDv7) | Globally unique. Trax IO uses for deduplication. |
| `tenant_id` | string | Lowercase, kebab-case, matches the TLS certificate's CN. |
| `kind` | enum | One of the seven event kinds. |
| `occurred_at` | RFC 3339 timestamp (UTC, milliseconds) | When the underlying business event happened in eMRO. |
| `schema_version` | semver string | Pinned to envelope + payload schema version. |
| `produced_at` | RFC 3339 timestamp (UTC, milliseconds) | When eMRO emitted the event. May lag `occurred_at` for batched producers. |
| `producer.system` | string | Always `emro` for v1. Reserved for future producers. |
| `producer.version` | string | The eMRO release that emitted the event. |
| `producer.instance` | string | The eMRO deployment identifier. |
| `payload` | object | Event-kind-specific. |

**Optional envelope fields:**

| Field | Type | Description |
|---|---|---|
| `correlation_id` | string | If the event was triggered by an upstream Trax IO request, this echoes that request's ID. |
| `causation_id` | string | The `event_id` of the event that caused this one (e.g., `wo_scheduled` causing `removal_recorded`). |

---

## The seven event kinds

### 1. `flight_completed`

Fires when an aircraft completes a revenue flight and the actuals are recorded in `AC_ACTUAL_FLIGHTS`.

```json
{
  "kind": "flight_completed",
  "payload": {
    "tail": "C-FABC",
    "ac_type": "A320",
    "destination": "YYZ",
    "origin": "LHR",
    "flight_hours": 7.42,
    "cycles": 1,
    "flight_date": "2026-04-14"
  }
}
```

**Source eMRO table:** `AC_ACTUAL_FLIGHTS` joined to `AC_MASTER`.
**Trax IO consumer:** Causal Demand Forecaster (v2). Updates per-tail and per-AC-type utilization features.
**Frequency estimate:** ~10–50 events per tail per month at typical utilization.

### 2. `stock_moved`

Fires on any change to `PN_INVENTORY_DETAIL` quantity that crosses a location boundary or condition state.

```json
{
  "kind": "stock_moved",
  "payload": {
    "pn": "NSN-12345",
    "sn": "SN-9876543",
    "from_location": "YYZ-MAIN",
    "to_location": "YYZ-LINE-A1",
    "from_condition": "NEW",
    "to_condition": "RESERVED",
    "qty": 1,
    "transaction_type": "RESERVATION",
    "transaction_no": 88412,
    "wo": "WO-2026-04-1042",
    "moved_by": "user-yyz-mech-42"
  }
}
```

**Source eMRO table:** `PN_INVENTORY_HISTORY` (CDC).
**Trax IO consumer:** AOG Risk Agent (v3) for shortage detection; current-stock cache invalidation in DynamoDB online feature store.
**Frequency estimate:** thousands per day per tenant at scale.

### 3. `wo_scheduled`

Fires when a work order is created or its scheduled start date changes.

```json
{
  "kind": "wo_scheduled",
  "payload": {
    "wo": "WO-2026-04-1042",
    "tail": "C-FABC",
    "ac_type": "A320",
    "location": "YYZ-MAIN",
    "wo_type": "C-CHECK",
    "scheduled_start": "2026-05-01T08:00:00Z",
    "scheduled_end": "2026-05-08T18:00:00Z",
    "estimated_duration_days": 7,
    "primary_eo": "EO-2026-0301"
  }
}
```

**Source eMRO table:** `WO`, `WO_ENGINEERING_ORDER`, `PLANNING`.
**Trax IO consumer:** Forecasting Agent (v2 causal), AOG Risk Agent (v3).
**Frequency estimate:** ~10–100 per day per tenant.

### 4. `vendor_price_changed`

Fires when an `ACTIVE` row in `PN_VENDOR_PRICE` is updated for any of: `price`, `lead_days`, `prefer`, `condition`, `status`.

```json
{
  "kind": "vendor_price_changed",
  "payload": {
    "pn": "NSN-12345",
    "vendor": "VEND-LH",
    "condition": "NEW",
    "old_price": 4500.00,
    "new_price": 5100.00,
    "currency": "USD",
    "old_lead_days": 14,
    "new_lead_days": 21,
    "preferred": true,
    "effective_date": "2026-05-01"
  }
}
```

**Source eMRO table:** `PN_VENDOR_PRICE`.
**Trax IO consumer:** Sourcing Agent (v5), Forecasting Agent (lead-time distribution).
**Frequency estimate:** ~10–100 per week per tenant.

### 5. `plan_published`

Fires when a forward-looking maintenance or flight plan is published or revised.

```json
{
  "kind": "plan_published",
  "payload": {
    "plan_id": "PLAN-2026-Q3-AC",
    "plan_type": "MAINTENANCE_PROGRAM",
    "fleet": "A320",
    "horizon_days": 180,
    "effective_from": "2026-07-01",
    "revision": 3
  }
}
```

**Source eMRO module:** Planning module (`PLANNING` table aggregations).
**Trax IO consumer:** Causal Demand Forecaster (v2) for forward demand projection.
**Frequency estimate:** ~1–5 per month per tenant.

### 6. `removal_recorded`

Fires when a rotable removal is recorded in `AC_PN_TRANSACTION_HISTORY` with `transaction_type = 'REMOVE'`.

```json
{
  "kind": "removal_recorded",
  "payload": {
    "pn": "LRU-CFM56-HPT-BLADE",
    "sn": "SN-9876543",
    "tail": "C-FABC",
    "ac_type": "A320",
    "location": "YYZ-MAIN",
    "wo": "WO-2026-04-1042",
    "task_card": "TC-04-12-BLADE-INSP",
    "removal_reason": "Engine borescope finding — leading edge erosion",
    "schedule_category": "UN/SCHEDULE",
    "reason_category": "WEAR",
    "removed_at": "2026-04-14T17:42:18Z"
  }
}
```

**Source eMRO table:** `AC_PN_TRANSACTION_HISTORY` (CDC, filtered).
**Trax IO consumer:** Forecasting Agent — wash rate updates and intermittent-demand model state. AOG Risk (v3) — current-shortage detection.
**Frequency estimate:** thousands per day per tenant.
**Security note:** `removal_reason` is free-text mechanic-authored. Trax IO consumer must treat as untrusted input — scrub before any LLM prompt construction. See ADR-0003 prompt-injection notes.

### 7. `eo_published`

Fires when an Engineering Order is issued, modified, or revoked. Includes airworthiness directives and service bulletins.

```json
{
  "kind": "eo_published",
  "payload": {
    "eo_number": "EO-2026-0401",
    "ata_chapter": "32",
    "ata_subchapter": "32-41",
    "affected_fleet": "A320",
    "affected_pn_pattern": "WHEEL-A320-MLG-*",
    "criticality": "AD",
    "compliance_due": "2026-09-30",
    "compliance_threshold_hours": 1500,
    "compliance_threshold_cycles": 1000,
    "issued_by": "TC-CIVIL-AVIATION",
    "issued_at": "2026-04-14T00:00:00Z",
    "title": "Mandatory inspection and replacement of A320 MLG wheel assembly..."
  }
}
```

**Source eMRO table:** `WO_ENGINEERING_ORDER`.
**Trax IO consumer:** AOG Risk (v3), Causal Demand Forecaster (v2). Triggers immediate event-lane recompute for all affected `PN × Location` slices.
**Frequency estimate:** ~5–20 per month per tenant; bursty around regulator AD issuance.
**Criticality enum:** `"AD" | "SB" | "FLEET_CAMPAIGN" | "OTHER"`.
**Special handling:** For `criticality = "AD"`, Trax IO bypasses the normal nightly cadence and runs hot-parts recompute within 5 minutes for every affected `PN × Location`.

---

## Ordering and consistency guarantees

- **Per-`tenant_id`:** Events are delivered in `produced_at` order. Out-of-order delivery within a tenant's stream is a contract violation.
- **Per-`(tenant_id, kind)`:** Same as above; producers must serialize emission per kind to preserve per-entity ordering.
- **Across tenants:** No ordering guarantee.
- **At-least-once delivery.** Trax IO is responsible for deduplication using `event_id`.
- **Causal ordering.** When `causation_id` is present, the consumer should not act on the caused event until the causing event has been processed. Trax IO implements this via in-memory wait-and-retry with a 30-second timeout.

---

## Retry, dead-lettering, and back-pressure

**Producer retry policy (eMRO).** On HTTP 5xx response or connection error: retry with exponential backoff (1s, 2s, 4s, 8s, 16s, 32s, 60s) up to 7 attempts. After 7 attempts: write to a local persistent dead-letter queue and alert the tenant's eMRO operator. Trax IO provides a `POST /v1/tenants/{tenant_id}/events/replay` endpoint that the operator can call once Trax IO is healthy.

**Consumer back-pressure (Trax IO).** On sustained 5xx from Trax IO, eMRO must respect `Retry-After` headers (in seconds). Trax IO will return `429 Too Many Requests` with `Retry-After` if the per-tenant rate exceeds 1000 events/second.

**4xx responses are terminal.** Producer must not retry. Producer must log and alert.

**Trax IO dead-letter handling.** Events accepted with HTTP 202 but failing downstream processing land in a per-tenant DLQ (S3-backed) with retention of 30 days. A Planner UI surface (sub-plan #7) shows DLQ depth and lets the planner trigger replay.

---

## Response codes

| Status | Meaning | Producer action |
|---|---|---|
| 202 Accepted | Event accepted, processing async. | Mark event as delivered. |
| 400 Bad Request | Schema validation failure. Body includes JSON Schema error detail. | Do not retry. Log. Alert. |
| 401 Unauthorized | TLS cert invalid or absent. | Do not retry. Page on-call. |
| 403 Forbidden | `tenant_id` does not match the cert's CN, or tenant is suspended. | Do not retry. Page on-call. |
| 409 Conflict | Duplicate `event_id` already processed. | Mark as delivered (idempotent). |
| 429 Too Many Requests | Rate limit. `Retry-After` header included. | Back off as instructed. |
| 5xx | Trax IO transient failure. | Retry per policy. |

---

## Schema versioning

- `schema_version` in the envelope is the version of the *combined* envelope + payload schema.
- Breaking changes (field removed, type changed, required-field added) require a major version bump (1.x.x → 2.0.0).
- Additive changes (new optional field) are minor (1.0.x → 1.1.0).
- Cosmetic changes (description-only, examples) are patch (1.1.0 → 1.1.1).
- Trax IO consumer accepts any `schema_version` in the same major series; producer should emit the highest version it supports.
- The deprecation policy: a major version is supported for one full eMRO release cycle (~6 months) after a successor is published.

---

## SOC 2, audit, and PII

- Every accepted event is mirrored verbatim to an immutable per-tenant S3 audit bucket (Object Lock, Compliance mode, 7-year retention) within 60 seconds of acceptance.
- The `produced_at` and `received_at` timestamps are both retained for latency audit.
- No PII fields are expected in any event payload. `removal_reason` and `title` are free-text fields and *might* contain incidental PII (mechanic name in the reason text). Trax IO scrubs known PII patterns (email, phone, ID-number patterns) before storing in observability indices, but the audit bucket retains the original.
- Customer can request a per-event audit trail via a Trax IO Planner UI surface (sub-plan #7).

---

## Operational expectations

- **Trax IO endpoint SLO:** 99.9% availability per calendar month. p95 acknowledgment latency < 200ms, p99 < 1s.
- **eMRO producer SLO:** Events emitted within 60 seconds of the underlying business event for `flight_completed`, `removal_recorded`, `stock_moved`, `vendor_price_changed`. Within 5 minutes for `wo_scheduled`, `plan_published`, `eo_published`.
- **Joint on-call.** A shared PagerDuty service routes contract-validation failures (4xx spike, schema-version regression, DLQ growth) to both teams.
- **Quarterly contract review.** Both teams review event volumes, schema usage, and consumer needs; new fields and event kinds are scoped here.

---

## Test harness

A FastAPI implementation of this contract (`fake_event_endpoint`) ships in the Spine repo and is the reference target for sub-plan #3 implementation. A separate `fake_event_publisher` (a CLI that emits all seven event kinds with valid payloads) ships for the eMRO team to exercise their consumer-side wiring before integrating with the real Trax IO endpoint.

Schemathesis nightly runs validate that both `fake_event_endpoint` and the production endpoint accept and reject the same payloads.

---

## Open questions

- **Should `correlation_id` be mandatory for `wo_scheduled` events triggered by Trax IO recommendations being applied?** Probably yes; defer until sub-plan #6 (Writeback REST) is in design — that's where the upstream `correlation_id` is minted.
- **Compression of audit bucket payloads.** S3 Intelligent-Tiering with Glacier Instant Retrieval after 90 days; revisit after first quarter of operation.
- **Should the contract publish a JSON Schema endpoint?** Yes — `GET https://events.trax-io.aws.trax.com/v1/schemas/{kind}/{version}` returns the JSON Schema. Add to v1.1.

---

## Sign-off

| Party | Role | Date | Signature |
|---|---|---|---|
| eMRO product team lead | Producer owner | | |
| Trax IO platform lead | Consumer owner | | |
| SecOps | mTLS + audit review | | |
| Customer pilot CIO sponsor | Tenant-side acceptance | | |

Sign-off required before sub-plan #3 enters Phase 2 and before sub-plan #4 Phase 11 deploys to lighthouse customer.
