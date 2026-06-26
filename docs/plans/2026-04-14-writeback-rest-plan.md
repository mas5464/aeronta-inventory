# Sub-plan #6 — eMRO Writeback REST API Implementation Plan

**Goal:** Ship the new REST surface inside eMRO that the Trax IO Writeback Agent calls to update `PN_INVENTORY_LEVEL`. Authenticated by Trax service principal (mTLS + bearer token), transactional, fully audit-logged, with one-click rollback and idempotency-key semantics that match the `fake_emro` contract shipped in sub-plan #4 Task 26.

**Owner:** eMRO product team.

**Format:** Like sub-plan #3, this is an integration-contract-driven work plan. The Trax IO platform team supplies the OpenAPI spec, contract test suite, and security requirements; the eMRO team implements in Java + Spring Boot against their own conventions.

**Tech Stack:** Java 17, Spring Boot (REST surface aligned with eMRO's existing controllers), Oracle DB with new `PN_INVENTORY_LEVEL_HISTORY` table, Bucket4j for rate limiting, Spring Security for mTLS + bearer token, MapStruct for DTO mapping.

---

## The contract (abbreviated — full OpenAPI spec at `docs/contracts/2026-04-14-emro-writeback-rest-contract.yaml`)

### `PUT /v1/tenants/{tenant_id}/inventory-level/{pn}/{location}`

**Auth:** mTLS (Trax-issued client cert) + `X-Service-Principal: trax-io` header + short-lived bearer token in `Authorization`.

**Headers:**
- `Idempotency-Key` (required): stable per recommendation; repeating the same key returns the first response verbatim.
- `X-Service-Principal: trax-io`.
- `Authorization: Bearer <JWT>`.

**Request body:**
```json
{
  "rop": 5,
  "eoq": 3,
  "safety_stock": 2,
  "max_stock": 9,
  "provenance_id": "prov-018f7b2c..."
}
```

**Validation:**
- `rop, eoq, safety_stock, max_stock` are non-negative integers.
- `rop >= safety_stock`.
- `max_stock >= rop + eoq`.
- `tenant_id`, `pn`, `location` must exist in eMRO's `LOCATION_MASTER` and `PN_MASTER`.

**Response 200 OK:**
```json
{
  "tenant_id": "aircanada",
  "pn": "LRU-CFM56-HPT-BLADE",
  "location": "YYZ-MAIN",
  "old_values": {"rop": 4, "eoq": 3, "safety_stock": 2, "max_stock": 8},
  "new_values": {"rop": 5, "eoq": 3, "safety_stock": 2, "max_stock": 9},
  "written_at": "2026-04-14T18:02:44.123Z"
}
```

**Error responses:**
- `400` invalid body / validation failure.
- `401` missing auth.
- `403` service principal mismatch or tenant not authorized.
- `404` PN or location unknown.
- `409` conflict — see Idempotency behavior.
- `422` business rule violation (e.g., `MinOQ` floor breached per `PN_VENDOR_PRICE`).
- `429` rate limit (100 writes/second per tenant default).
- `5xx` transient — Trax IO retries.

**Idempotency:** The same `Idempotency-Key` + same body returns 200 with the original response. Same key + different body returns 409 with a `body_mismatch` reason. Keys are retained 30 days.

### `GET /v1/tenants/{tenant_id}/inventory-level/{pn}/{location}/history`

Paginated history of writes for this `(tenant, pn, location)`.

### `POST /v1/tenants/{tenant_id}/inventory-level/{pn}/{location}/rollback`

Reverts to the immediately-prior value. Requires `X-Planner-Principal` header (not a service-principal call — only the customer's planner can authorize). Emits a history record.

### `POST /v1/tenants/{tenant_id}/inventory-level/bulk-rollback`

Body: `{"since": "ISO8601", "filter": {"criticality_tier": ..., "pn_pattern": ...}}`. Reverts all writes matching the filter since the timestamp. Requires planner principal + confirmation token (24h TTL) — this is the "we broke it, undo everything" button. Logged loudly.

---

## Implementation phases

### Phase 0: OpenAPI review + DB migration design (2 weeks)

- eMRO team reads the OpenAPI spec and `fake_emro` source (sub-plan #4 Task 26) as the reference implementation.
- Design `PN_INVENTORY_LEVEL_HISTORY` schema with eMRO DBA:
 - PK = `(tenant_id, pn, location, version)`; version is a monotonically increasing per-key integer.
 - Columns: `old_values` (JSON), `new_values` (JSON), `changed_by_agent` (string), `agent_version` (string), `provenance_id` (string), `tier` (string: ADVISOR/BOUNDED/AUTONOMOUS), `changed_by_principal` (string), `changed_at` (timestamp), `idempotency_key` (string, nullable), `parent_version` (FK to previous version for rollback tracking).
 - Indexes on `(tenant_id, changed_at)` for history queries and `(tenant_id, idempotency_key)` for idempotency lookups.
- Sign-off on schema with SecOps and with Trax IO platform team.

### Phase 1: Authentication + authorization (3 weeks)

- mTLS termination at the API gateway in front of eMRO (or in eMRO's embedded HTTPS layer, depending on deployment topology).
- Spring Security filter chain:
 - Validates Trax-issued client certificate CN against a per-tenant allow-list.
 - Validates bearer-token JWT signed by Trax IO's token-issuer service.
 - Validates `tenant_id` path param matches both cert CN and JWT `tenant` claim. All three must agree; any mismatch → 403.
- Rate limiter per tenant (Bucket4j, 100 writes/sec default, tenant-configurable).

### Phase 2: Write endpoint (4 weeks)

- `PUT /v1/tenants/{tenant_id}/inventory-level/{pn}/{location}` implemented as a Spring `@RestController`.
- Business logic in a service bean:
 - Idempotency check first (query `PN_INVENTORY_LEVEL_HISTORY` by `(tenant_id, idempotency_key)`).
 - If found, return the stored response verbatim (200).
 - Otherwise, begin a DB transaction.
 - Validate PN + location exist.
 - Validate business rules (MinOQ floor from `PN_VENDOR_PRICE`, shelf-life constraint, etc. — these duplicate some of sub-plan #5's constraint logic, deliberately, so eMRO is the hard-guarantor even if Trax IO's Guardrail has a bug).
 - Read current `PN_INVENTORY_LEVEL` row (or create if absent).
 - Update `PN_INVENTORY_LEVEL` with new values.
 - Insert `PN_INVENTORY_LEVEL_HISTORY` row with provenance, idempotency key, tier, principal.
 - Commit.
 - Emit a `stock_level_changed` event to sub-plan #3's event publisher (new event kind, added in v1.1 of the event contract).
 - Return response DTO.
- Transactional integrity enforced at Spring `@Transactional` boundary.

### Phase 3: History + rollback endpoints (3 weeks)

- `GET /v1/tenants/{tenant_id}/inventory-level/{pn}/{location}/history` with pagination by `changed_at`.
- `POST /.../rollback` — restores prior version, emits a new history record with `parent_version` = prior version, `new_values` = prior `new_values`, `changed_by_principal` = planner.
- `POST /.../bulk-rollback` — filter + confirmation-token flow. Planner invokes first time to get a confirmation token; second call with the token and same filter executes the rollback. Both calls are audit-logged.

### Phase 4: 90-day rollback window enforcement (1 week)

- Rollback rejected if prior version is older than 90 days (configurable per tenant, minimum 90d — cannot be set to zero).

### Phase 5: Contract tests against fake_emro (2 weeks)

- Pull the shared contract test suite from sub-plan #4.
- Spin up the eMRO app in an integration-test harness; run the same scenarios that `fake_emro` passes.
- Any divergence between `fake_emro` and the real eMRO endpoint breaks CI on both sides.
- Schemathesis nightly against the deployed staging eMRO.

### Phase 6: Planner UI integration (aligned with sub-plan #7)

- Sub-plan #7 (Planner UI) provides the "rollback this write" and "bulk rollback" buttons. They call these endpoints with the planner principal.

### Phase 7: Staged rollout with lighthouse customer (4 weeks)

- Deploy to lighthouse customer in "advisor-only" mode: Trax IO writes never reach this API — every recommendation goes to the approval queue.
- After 14 days of clean Planner UI operation: enable Tier C writes for the narrowest part class (tier-5 consumables under $100). Watch for 14 days.
- Gradually expand tier/cost/delta bands per sub-plan #10's onboarding runbook.

### Phase 8: Ship in eMRO release train

- Cut into the same eMRO release as sub-plan #7 Planner UI when possible; sequencing decided with eMRO product manager.

---

## Deliverables from the eMRO team

1. Java module `trax-writeback-rest-X.Y.Z.jar`.
2. DB migrations for `PN_INVENTORY_LEVEL_HISTORY`.
3. Security review package documenting auth chain.
4. Runbook for incident response (rollback, bulk rollback, rate-limit exhaustion).
5. Schemathesis + contract-test report (green).
6. Load-test report (1000 req/sec sustained without lock contention on `PN_INVENTORY_LEVEL`).
7. Release notes.

## Deliverables from the Trax IO platform team (supporting)

1. OpenAPI 3.1 spec at `docs/contracts/2026-04-14-emro-writeback-rest-contract.yaml`.
2. Token-issuer service (small Lambda + API Gateway) that mints JWTs for Writeback Agent.
3. Per-tenant mTLS certificate issuance runbook.
4. Shared contract test suite (Java + Python bindings).
5. Planner UI rollback button wiring (sub-plan #7).

---

## Risks + mitigations

- **Lock contention on `PN_INVENTORY_LEVEL`** under high write load — mitigated by row-level locking, the per-tenant rate limiter, and the tiered-autonomy design that throttles Tier C throughput.
- **Duplicate-write scenarios** where the same `Idempotency-Key` is seen with a different body due to a Trax-side bug — 409 response + alert on both sides.
- **Business rule divergence** between Trax IO's Guardrail Agent and eMRO's server-side validation — deliberately redundant; eMRO is the last line of defense. Log and alert when eMRO rejects something Trax IO Guardrail approved.
- **Auth chain complexity** (mTLS + JWT + tenant match) — thorough negative testing required; any of the three failing produces 401/403 with a specific reason code.

## Estimated timeline

- Phase 0–5: 15 weeks (2 eMRO engineers).
- Phase 6: parallel with sub-plan #7, 4 weeks.
- Phase 7: 4 weeks.
- Phase 8: per release cadence.

**Earliest production writes at lighthouse customer:** ~5 months after Phase 0 kickoff, assuming the eMRO release train supports.

**Impact on sub-plan #4 (Agent Spine):** Spine writes against `fake_emro` until this ships. Shadow mode and dry-run mode unaffected.
