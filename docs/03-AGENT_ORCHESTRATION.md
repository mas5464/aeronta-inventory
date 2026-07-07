# Trax IO Inventory Optimizer — Agent Organization & Orchestration

**Audience:** Architects, Data Engineers, ML Ops, Bedrock/Strands Operators  
**Last Updated:** 2026-07-07  
**Focus:** Agent roles, communication patterns, orchestration flow, error handling

---

## Table of Contents

1. [Agent Roster & Responsibilities](#agent-roster--responsibilities)
2. [Communication Patterns](#communication-patterns)
3. [Orchestration Flow (Happy Path)](#orchestration-flow-happy-path)
4. [Error Handling & Fallbacks](#error-handling--fallbacks)
5. [Multi-Tenant Isolation](#multi-tenant-isolation)
6. [Observability & Audit Trail](#observability--audit-trail)
7. [Scaling Considerations](#scaling-considerations)

---

## Agent Roster & Responsibilities

### Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                  Orchestration Entry Point                       │
│               (Agent Spine, CLI or REST API)                     │
└────────────────┬─────────────────────────────────────────────────┘
                 │ "Recommend for tenant=aircanada, week=2026-W27"
                 ▼
        ┌────────────────────┐
        │   SUPERVISOR       │
        │  (Claude Sonnet)   │
        │                    │
        │ • Parse request    │
        │ • Route specialists│
        │ • Aggregate results│
        │ • Enforce guardrails
        │ • Emit decisions   │
        └────┬───┬───┬──────┘
            /   |   \
           /    |    \
      ┌───▼─┐ ┌┴─────┴──┐ ┌─────▼──┐
      │DATA │ │ POLICY  │ │ANALYST │
      │     │ │EVALUATOR│ │        │
      └──┬──┘ └────┬────┘ └───┬────┘
         │        │           │
         ├────────┼───────────┤
         │        │           │
    ┌────▼─┐ ┌───▼───┐  ┌────▼──────┐
    │Stat  │ │Regime │  │Guardrail  │
    │Proj  │ │Router │  │Evaluator  │
    └──┬───┘ └───┬───┘  └────┬──────┘
       │         │            │
       └─────────┼────────────┘
               ┌─▼──────────────────────────┐
               │   EXECUTORS (Tier-aware)   │
               │                            │
               │ • Write to ledger          │
               │ • Create requisitions      │
               │ • Create transfers         │
               │ • Emit events to Kafka     │
               │ • Trigger shadowings       │
               └─▼──────────────────────────┘
               
               ▼ (per part-location pair)
         ┌──────────────┐
         │Ledger Entry  │
         │(audit trail) │
         └──────────────┘
```

### 1. Supervisor Agent (Claude Sonnet 4.6)

**Role:** Decision-maker; orchestrates the entire flow  
**Inputs:**
- Batch recommendation request: `{tenant_id, week_id, service_level, constraints}`
- Feature context (from Data Specialist)
- Regime classifications (from Regime Router)

**Key Responsibilities:**

1. **Route Specialists** — Dispatch data fetching, policy evaluation, demand forecasting to parallel specialists
2. **Aggregate Results** — Combine outputs into a single recommendation per part-location
3. **Enforce Guardrails** — Apply tier-based constraints, escalation rules, exception handling
4. **Generate Explanations** — Chain-of-thought reasoning for each recommendation (for audit)
5. **Emit Decisions** — Format output as recommendation records ready for ledger storage

**Output:**
```json
{
  "run_id": "run-2026-W27-aircanada",
  "recommendations": [
    {
      "pn": "A380-TIRE-MAIN",
      "location": "YYZ",
      "rop": 15,
      "eoq": 45,
      "safety_stock": 8,
      "max": 68,
      "tier": "B",
      "confidence": 0.92,
      "reasoning": "Seasonal demand pattern detected; boosted projector forecast 1.8 units/month; ROP increased 20% per Q2 peak. Within Tier B guardrails."
    }
  ],
  "exceptions": [
    {
      "pn": "A380-HYDRAULIC-PUMP",
      "location": "YYZ",
      "reason": "Confidence below 70% (insufficient historical data); recommend manual review",
      "escalation_tier": "DATA_QUALITY"
    }
  ],
  "statistics": {
    "total_parts_evaluated": 847,
    "recommendations_auto_approved": 821,
    "exceptions_requiring_escalation": 26,
    "model_mix": {
      "statistical_projector": 587,
      "gradient_boosted_projector": 235,
      "empirical_bayes": 25
    }
  }
}
```

### 2. Data Specialist (Claude Haiku 4.5)

**Role:** Lightweight data fetcher & validator  
**Responsibility:** Query feature store & eMRO; validate schema; return structured context

**Methods:**

```python
# Pseudo-interface
class DataSpecialist:
    
    async def fetch_part_context(
        tenant_id: str, 
        pn: str, 
        location: str
    ) -> PartContext:
        """
        Returns:
        {
          pn, location,
          current_level: {on_hand, on_order, rop_old, eoq_old, ss_old, max_old},
          recent_changes: [{date, old_values, new_values, principal}],  # last 5
          criticality_tier: Tier,
          shelf_life_days: int | None,
          hazmat_class: str | None,
          supplier: str,
          lead_time_mean: float,
          lead_time_std: float,
          unit_cost: float,
        }
        """
        pass
    
    async def fetch_demand_timeseries(
        pn: str, 
        location: str,
        window_months: int = 24
    ) -> DemandTimeSeries:
        """
        Returns:
        {
          dates: [date],
          quantities: [int],
          seasonality_index: [float],  # 0.5..2.0 relative to mean
          trend: float,  # monthly % change
          anomaly_score: [float],  # 0..1, high = unexpected spike
          confidence: float,  # % of data points available (vs. expected)
        }
        """
        pass
    
    async def validate_forecast(
        historical: DemandTimeSeries,
        forecast: [float]
    ) -> ForecastValidation:
        """
        Sanity checks:
        - Forecast not negative
        - Forecast std dev within 2x historical std dev
        - No extreme jumps (>5x historical max)
        
        Returns: (is_valid: bool, warnings: [str])
        """
        pass
```

**Why Haiku (vs. Sonnet)?**

- Data fetching is deterministic & unambiguous (query DB, return JSON)
- Haiku is 4x faster & cheaper for structured extraction
- Supervision by Sonnet ensures data quality before decision-making

### 3. Regime Router (Classifier, Embedded in Services)

**Role:** Detect demand pattern type  
**Input:** DemandTimeSeries (24-month history)  
**Output:** Regime label + confidence

```python
class RegimeRouter:
    """
    Classify demand into: INTERMITTENT | SEASONAL | HIGH_FREQ | SPARSE | ANOMALOUS
    """
    
    def classify(timeseries: DemandTimeSeries) -> (regime: str, confidence: float):
        """
        Logic:
        1. Check sparsity: if >50% zero months → INTERMITTENT
        2. Check seasonality: if seasonal index amplitude >0.3 → SEASONAL
        3. Check frequency: if >3 orders/month → HIGH_FREQ
        4. Check anomaly score: if any point anomaly >0.8 → ANOMALOUS
        5. Else → SPARSE (0 history) or fallback
        
        Return regime + confidence (how sure we are about the classification)
        """
        
        zero_rate = (timeseries.quantities == 0).sum() / len(timeseries)
        if zero_rate > 0.5:
            return ("INTERMITTENT", 1.0 - zero_rate)
        
        seasonality_amplitude = (
            timeseries.seasonality_index.max() 
            - timeseries.seasonality_index.mean()
        )
        if seasonality_amplitude > 0.3:
            return ("SEASONAL", 0.8)
        
        # ... more logic
        
        return ("HIGH_FREQ", 0.7)
```

**Routing Decision:**

| Regime | Projector | Lead Time | Fallback |
|---|---|---|---|
| INTERMITTENT | Statistical | 200ms | Naive (mean) |
| SEASONAL | Statistical | 300ms | Naive (seasonal mean) |
| HIGH_FREQ | Gradient-Boosted | 500ms | Statistical (fallback) |
| SPARSE | Empirical Bayes | 50ms | Domain prior |
| ANOMALOUS | Escalate to human | N/A | Ask Sonnet |

### 4. Policy Evaluator (Rule Engine, Deterministic)

**Role:** Apply guardrails, tier constraints, business rules  
**Stateless:** Same input → same output, every time

```python
class PolicyEvaluator:
    """
    Apply Tier A/B/C rules, shelf-life constraints, hazmat limits, etc.
    """
    
    def evaluate(
        recommendation: {rop, eoq, ss, max},
        part_context: PartContext,
        policy_version: str = "v1"
    ) -> EvaluatedRecommendation:
        """
        Returns:
        {
          original_recommendation,
          applied_constraints: [str],  # "shelf_life_cap", "hazmat_qty_reduced", ...
          final_recommendation: {rop, eoq, ss, max},
          escalation_needed: bool,
          escalation_reason: str | None,
          confidence_impact: float,  # how much guardrails reduced confidence
        }
        """
        
        # Apply constraints in sequence
        r = recommendation
        
        # Tier-based delta check
        tier = part_context.criticality_tier
        rop_delta = (r.rop - part_context.current_level.rop_old) / part_context.current_level.rop_old
        
        if tier == 'A' and abs(rop_delta) > 0.50:
            return EvaluatedRecommendation(
                escalation_needed=True,
                escalation_reason=f"Tier A part ROP increase {rop_delta:.0%} exceeds +50% guardrail"
            )
        
        # Shelf-life capping
        if part_context.shelf_life_days and part_context.shelf_life_days < 365:
            max_inventory = part_context.daily_demand * (part_context.shelf_life_days * 0.6)
            r.eoq = min(r.eoq, max_inventory)
            r = r.with_applied_constraint("shelf_life_cap")
        
        # Hazmat quantity limits
        if part_context.hazmat_class == 3:  # Flammable
            r.eoq = min(r.eoq, 220)  # 220L legal limit
            r = r.with_applied_constraint("hazmat_qty_reduced")
        
        return EvaluatedRecommendation(
            escalation_needed=False,
            final_recommendation=r,
            applied_constraints=r.constraints_applied
        )
```

### 5. Analyst Agent (Claude Sonnet, Exception Handler)

**Role:** Handle edge cases, anomalies, missing data  
**Invoked:** Only when exceptions are detected

**Examples:**

```
Scenario 1: Zero Demand for 24 Months
  Analyst reasoning: "Part may be obsolete or already superseded.
                     Check: Does PN still appear in maintenance manuals?
                     Action: Recommend escalation; do not auto-approve."

Scenario 2: Supplier Discontinued Part
  Analyst: "Historical lead time data outdated. 
            Recommendation may be invalid.
            Action: Flag for procurement team."

Scenario 3: Conflicting Demands (high SL vs. low cost)
  Analyst: "User requested 99% SL but also <5% cost increase.
            Mathematically impossible for this part.
            Action: Ask user to clarify priority."
```

---

## Communication Patterns

### 1. Synchronous (Request-Reply): REST API

```
User/Client
    │
    ├─ POST /v1/recommend
    │  {tenant_id, week_id, service_level, constraints}
    │
    ▼ (Supervisor Agent)
    │
    ├─ Parallel fetch from Data Specialist
    ├─ Parallel regime routing
    ├─ Parallel projection (stat + boosted)
    ├─ Sequential policy evaluation
    │
    ▼ (within 30 seconds)
    │
    └─ 200 OK
       {recommendations: [...], exceptions: [...], statistics: {...}}
```

**Latency SLA:** <30 seconds for 5,000-part batch (P95)

### 2. Asynchronous (Event-Driven): Kafka

```
Recommendation Written to eMRO
    │
    ├─ Event emitted to topic: "optimizer.writeback.v1"
    │  {domain: 'STOCK_LEVEL', pn, location, new_values, tenant_id, run_id}
    │
    ▼ (Write-back Service consumes)
    │
    ├─ Validate constraints
    ├─ Insert/update PN_INVENTORY_LEVEL
    ├─ Record in WRITEBACK_LEDGER
    │
    ▼ (Acknowledge)
    │
    └─ Emit to "optimizer.writeback.results.v1"
       {pn, location, status: WRITTEN|ERROR, message, version}
       
    ▼ (Results consumer can pick up for replay/audit)
```

**Topics:**

| Topic | Retention | Consumers | Purpose |
|---|---|---|---|
| `optimizer.writeback.v1` | 7 days | Writeback Service | Inbound action records |
| `optimizer.writeback.results.v1` | 3 days | Results Archiver | Per-row outcomes & audit |
| `optimizer.writeback.dlq.v1` | 30 days | Admin Dashboard | Dead-letter queue (infra errors) |

### 3. Feature Store (Pull Model): DynamoDB + S3 Iceberg

```
Specialist Agent
    │
    ├─ Query DynamoDB
    │  Key: {tenant_id, pn_location}
    │  Returns: latest cached features (20-minute old)
    │
    └─ If cache miss OR stale >1 hour:
       │
       ├─ Query S3 Iceberg (full history)
       │  SELECT * FROM demand_features WHERE pn=? AND location=?
       │
       └─ Update DynamoDB cache (TTL: 1 hour)
```

**Consistency Model:** Eventual (cache may lag by 20 min); acceptable for weekly rebalancing

---

## Orchestration Flow (Happy Path)

### Step-by-Step: "Recommend for Week 27, Air Canada"

```
1. User Request (REST API)
   ──────────────────────
   POST /v1/recommend
   {
     "tenant_id": "aircanada",
     "week_id": "2026-W27",
     "service_level_target": 0.95,
     "mode": "standard"  # vs. "shadow" for testing
   }
   
   ▼
   
2. Supervisor Initializes
   ──────────────────────
   Supervisor Agent (Sonnet) spawns:
   - run_id = "run-2026-W27-aircanada-001"
   - Start orchestration timer
   - Log to audit trail: "Orchestration started"
   
   ▼
   
3. Parallel Phase 1: Data Fetch
   ──────────────────────────────
   Launch 4 parallel Data Specialist instances:
   
   | Instance | Batch | Task |
   |---|---|---|
   | HS-1 | Parts 1-250 | Fetch from feature store |
   | HS-2 | Parts 251-500 | Fetch from feature store |
   | HS-3 | Parts 501-750 | Fetch from feature store |
   | HS-4 | Parts 751-1000 | Fetch from feature store |
   
   Wait for all to complete (P95: 5 seconds)
   
   ▼
   
4. Parallel Phase 2: Demand Forecasting
   ───────────────────────────────────
   Route each part to appropriate projector (based on regime):
   
   | Regime | Projector | Parts | Latency |
   |---|---|---|---|
   | INTERMITTENT | Statistical | 587 | 100ms each |
   | SEASONAL | Statistical | 156 | 100ms each |
   | HIGH_FREQ | Gradient-Boosted | 235 | 200ms each |
   | SPARSE | Empirical Bayes | 22 | 50ms each |
   
   Run in parallel; max wall-clock ≈ 250ms
   
   Output: forecast + confidence per part
   
   ▼
   
5. Sequential Phase 3: Policy Evaluation
   ──────────────────────────────────
   (Deterministic rule engine, no parallelization benefit)
   
   For each part:
   a. Apply tier-based guardrails
   b. Check shelf-life constraints
   c. Evaluate hazmat limits
   d. Compute escalation flags
   
   Latency: 50ms per part × 1,000 parts = 50 seconds (serial)
   Optimization: Batch rule evaluation on GPU (future)
   
   ▼
   
6. Exception Aggregation
   ──────────────────────
   Supervisor collects all escalations:
   - 26 parts need manual approval (cost > $10K impact)
   - 3 parts have confidence < 70% (data quality)
   - 1 part has discontinued supplier (schedule mismatch)
   
   For each, invoke Analyst Agent:
   "Analyze exception: A380-HYDRAULIC-PUMP, confidence 65%"
   → Analyst returns: "Recommend escalation to procurement"
   
   ▼
   
7. Aggregate & Format
   ───────────────────
   Supervisor constructs final response:
   
   {
     "run_id": "run-2026-W27-aircanada-001",
     "status": "completed",
     "duration_seconds": 23,
     "recommendations": [
       {
         "pn": "A380-TIRE-MAIN",
         "location": "YYZ",
         "rop": 15, "eoq": 45, "ss": 8, "max": 68,
         "tier": "B",
         "confidence": 0.92,
         "model": "statistical_projector"
       },
       ... (847 total)
     ],
     "exceptions": [...],  # 30 items needing escalation
     "statistics": { ... }
   }
   
   ▼
   
8. Write to Ledger (Trax IO Seam)
   ────────────────────────────
   
   On approval (shadow or real):
   For each recommendation:
     INSERT INTO WRITEBACK_LEDGER (
       tenant_id, pn, location, version, parent_version,
       domain, new_values, old_values, idempotency_key,
       outcome, message, created_at, created_by
     ) VALUES (
       'aircanada', 'A380-TIRE-MAIN', 'YYZ', 4, 3,
       'STOCK_LEVEL',
       '{"rop": 15, "eoq": 45, "ss": 8, "max": 68}',
       '{"rop": 10, "eoq": 40, "ss": 6, "max": 60}',  # old values
       'run-2026-W27-aircanada-001:A380-TIRE-MAIN:YYZ',
       'WRITTEN',  # or SHADOWED if shadow mode
       'Recommended by Supervisor; seasonal demand 1.8 units/month',
       SYSDATE,
       'system:optimizer'
     )
   
   ▼
   
9. Kafka Event Emission (if not shadow)
   ────────────────────────────────────
   
   For each written recommendation:
   PRODUCE TO optimizer.writeback.v1:
   {
     "domain": "STOCK_LEVEL",
     "tenant_id": "aircanada",
     "pn": "A380-TIRE-MAIN",
     "location": "YYZ",
     "new_values": {"rop": 15, "eoq": 45, "ss": 8, "max": 68},
     "idempotency_key": "run-2026-W27-aircanada-001:A380-TIRE-MAIN:YYZ",
     "run_id": "run-2026-W27-aircanada-001",
     "timestamp": "2026-07-06T14:30:00Z"
   }
   
   ▼
   
10. Write-Back Service Processes
    ─────────────────────────────
    (Java Quarkus service)
    
    For each Kafka message:
    a. Validate idempotency key (already written?)
    b. Fetch current PN_INVENTORY_LEVEL (old values)
    c. Insert new row with new values
    d. Insert audit record (PN_INVENTORY_LEVEL_AUDIT)
    e. Record in WRITEBACK_LEDGER with outcome=WRITTEN
    f. Emit to results topic:
       {pn, location, status: WRITTEN, version: 4}
    
    ▼
    
11. UI Reflects Changes (within 20s)
    ────────────────────────────────
    
    React app polls GET /v1/tenants/{tenant}/dashboard
    → BFF reads latest WRITEBACK_LEDGER
    → Dashboard updates: new ROP/EOQ figures, version numbers
    → Notification: "847 recommendations applied, 26 exceptions pending"
```

**Total Orchestration Time (P95):** 23 seconds end-to-end (data fetch + forecast + policy + ledger)

---

## Error Handling & Fallbacks

### 1. Data Fetch Failure

```
Scenario: DynamoDB unavailable (500 error)

Flow:
  1. Data Specialist tries DynamoDB → TimeoutError
  2. Falls back to S3 Iceberg query (slower, but reliable)
  3. If S3 also fails:
     - Use 7-day cache from local disk
     - Log WARNING: "Using stale data; confidence reduced 20%"
     - Continue with degraded confidence score
  4. If all fail:
     - Skip this part (don't include in batch)
     - Log this part as EXCEPTION: "Data unavailable"
     - Alert ops: "DynamoDB + S3 both down for 5 min"
```

### 2. Forecast Model Failure

```
Scenario: Gradient-Boosted Projector crashes (OOM)

Flow:
  1. Boosted projector: OutOfMemoryError
  2. Supervisor catches exception
  3. Falls back to Statistical Projector (lighter-weight)
  4. If Statistical also fails:
     - Use Empirical Bayes with wide CI (conservative)
  5. Log: "Boosted forecast failed; using statistical fallback"
     Confidence: -30%
  6. After incident:
     - Increase container memory
     - Reduce batch size
```

### 3. Tier Policy Conflict

```
Scenario: Supervisor wants to increase ROP 200%, but Tier A cap is +50%

Flow:
  1. Policy Evaluator detects violation
  2. Returns escalation_needed=True
  3. Supervisor sends to Analyst Agent:
     "Tier A part violation detected. Recommendation: ROP +200%, guardrail: +50%.
      Override? (requires human approval)"
  4. If user approves:
     - Write to WRITEBACK_LEDGER with override_principal="regional_mgr:ID"
     - Outcome = WRITTEN_WITH_OVERRIDE
  5. If user rejects:
     - Apply guardrail (limit to +50%)
     - Outcome = GUARDRAIL_APPLIED
```

### 4. Ledger Write Conflict

```
Scenario: Duplicate idempotency key (retransmission)

Flow:
  1. Writeback Service: "INSERT ... WHERE idempotency_key = 'X'"
  2. ORA-00001: unique constraint violation
  3. Classify exception:
     - Same TENANT_ID + IDP_KEY + VERSION (legitimate duplicate) → SKIPPED_DUPLICATE
     - Different VERSION (stale retry) → VERSION_CONFLICT_RETRY
  4. If SKIPPED_DUPLICATE:
     - Query existing row
     - Return original CREATED_REF (requisition/order number)
     - Emit to results topic: status=SKIPPED_DUPLICATE
  5. If VERSION_CONFLICT_RETRY:
     - Wait 1.1 seconds (allow CREATED_DATE to advance)
     - Retry (up to 3 times)
     - If still fails: emit to DLQ
```

---

## Multi-Tenant Isolation

### 1. Tenant Context (Everywhere)

```python
@dataclass
class TenantContext:
    """
    Every operation carries tenant isolation context.
    Passed through all agents, services, and persistence layers.
    """
    tenant_id: str  # "aircanada", "united", "deltaai", etc.
    kms_key_id: str  # AWS KMS CMK for this tenant
    
    # Assertions (runtime checks in every specialist)
    def assert_tenant_match(self, arg_tenant_id: str):
        if self.tenant_id != arg_tenant_id:
            raise TenantMismatchError(
                f"Context tenant {self.tenant_id} != arg tenant {arg_tenant_id}"
            )
```

### 2. Query Isolation (4-Layer Defense)

```sql
-- Layer 1: Explicit WHERE clause (application)
SELECT * FROM WRITEBACK_LEDGER WHERE tenant_id = 'aircanada'

-- Layer 2: Row-level security (database)
CREATE ROW LEVEL SECURITY POLICY ledger_rls ON WRITEBACK_LEDGER
  WHERE tenant_id = CURRENT_SESSION_TENANT_ID

-- Layer 3: KMS encryption key per tenant
-- Every ledger row encrypted with tenant's own CMK
-- Reading another tenant's data → KMS denies decryption

-- Layer 4: IAM role boundary (AWS)
-- Quarkus service assumes role with:
--   - s3:GetObject restricted to "arn:aws:s3:::trax-io-*/aircanada/*"
--   - kms:Decrypt restricted to "arn:aws:kms:...key/alias/trax-io/aircanada"
```

### 3. Tenant-Scoped Feature Store

```
Feature Store (DynamoDB):
  PK = tenant_id (partition key)
  SK = pn_location (sort key)
  
  Example queries (automatically isolated):
  
  -- Air Canada data only
  GET {
    "tenant_id": "aircanada",
    "pn_location": "A380-TIRE-MAIN#YYZ"
  }
  
  -- United data only
  GET {
    "tenant_id": "united",
    "pn_location": "A380-TIRE-MAIN#YYZ"
  }
  
  Both may have the SAME part @ same location,
  but completely isolated by tenant partition.
```

---

## Observability & Audit Trail

### 1. Distributed Tracing (OpenTelemetry)

```python
# Every orchestration call is traced

with tracer.start_as_current_span("orchestrate_batch") as span:
    span.set_attribute("tenant_id", "aircanada")
    span.set_attribute("batch_size", 1000)
    span.set_attribute("run_id", "run-2026-W27-aircanada-001")
    
    # Child spans
    with tracer.start_as_current_span("data_fetch"):
        # ... fetch from feature store
        pass
    
    with tracer.start_as_current_span("regime_routing"):
        # ... classify demand patterns
        pass
    
    with tracer.start_as_current_span("forecast_parallel"):
        # ... statistical + boosted projection
        pass
    
    with tracer.start_as_current_span("policy_evaluation"):
        # ... apply guardrails
        pass
    
    with tracer.start_as_current_span("ledger_write"):
        # ... persist to WRITEBACK_LEDGER
        pass

# Traces exported to AWS X-Ray (production) or Jaeger (local)
```

### 2. Audit Trail (Ledger-First Design)

```sql
-- WRITEBACK_LEDGER is the source of truth for ALL changes

SELECT id, tenant_id, pn, location, version, parent_version,
       domain, new_values, old_values, idempotency_key,
       created_at, created_by, outcome, message
FROM writeback_ledger
WHERE tenant_id = 'aircanada'
  AND created_at >= TRUNC(SYSDATE) - 7
ORDER BY created_at DESC;

-- Example output:
-- 5001, aircanada, A380-TIRE-MAIN, YYZ, 4, 3, STOCK_LEVEL, {...}, {...}, run-W27:pn:loc, 2026-07-06 14:30, system:optimizer, WRITTEN, "..."
-- 5000, aircanada, A380-TIRE-MAIN, YYZ, 3, 2, STOCK_LEVEL, {...}, {...}, run-W26:pn:loc, 2026-06-29 14:30, system:optimizer, WRITTEN, "..."
```

**Questions the audit trail answers:**

- ✓ "Who changed ROP from 10 to 15?" → `created_by` field
- ✓ "When did we write this recommendation?" → `created_at`
- ✓ "What was the previous ROP?" → `old_values.rop`
- ✓ "Why did we make this change?" → `message` (reason)
- ✓ "Can we roll back this change?" → Latest `WRITTEN` row w/ non-null `old_values`

### 3. Metrics & Alerting

```python
# Micrometer metrics exported to CloudWatch/Prometheus

# Per-run statistics
writeback.recommendations_total → 847 (counter)
writeback.exceptions_escalated → 26 (counter)
writeback.duration_seconds → 23 (histogram)

# Per-model statistics
writeback.statistical_projector.invocations → 743
writeback.gradient_boosted_projector.invocations → 235
writeback.empirical_bayes_projector.invocations → 22

# Per-tier statistics
writeback.tier_a_recommendations → 156
writeback.tier_b_recommendations → 512
writeback.tier_c_recommendations → 179

# Alerts (CloudWatch)
IF writeback.duration_seconds > 60 FOR 5 minutes
  THEN ALERT "Orchestration latency degraded"

IF writeback.exceptions_escalated > 50 (% of batch)
  THEN ALERT "High exception rate; check data quality"

IF writeback.dlq.messages > 10 (per hour)
  THEN ALERT "Infra errors in write-back; check Kafka/DB"
```

---

## Scaling Considerations

### 1. Parallel Batch Processing

```
Current: Single batch request → 1 Supervisor + N parallel Specialists
Target: 10 concurrent batches (Week 27 + 9 others, different tenants)

Architecture:
  • Supervisor pool: 10 instances (Bedrock Agents)
  • Each Supervisor: 4 parallel Data Specialists (Haiku)
  • Shared Feature Store: DynamoDB (auto-scaling)
  • Shared Kafka cluster: 3 brokers (MSK)
  • Write-back consumer group: 3 instances (auto-scale by lag)
```

### 2. Feature Store Caching Strategy

```
Tier 1 (Hot): DynamoDB, 20-minute TTL
  → Latest features, sub-100ms latency
  
Tier 2 (Warm): S3 Iceberg, full history
  → Weekly rebuild, 500ms latency, recovers from Tier 1 loss
  
Tier 3 (Cold): Archive to Glacier
  → Compliance archival, 12-hour retrieval SLA
```

### 3. Ledger Retention & Archival

```
Operational (hot):
  WRITEBACK_LEDGER table, partitioned by tenant_id + created_date
  → 90 days of data on hot storage
  
Archive (warm):
  S3 Iceberg table (daily snapshot)
  → 7 years retention (SOC 2 Type II requirement)
  
Purge (compliance):
  After 7 years, delete (unless under legal hold)
```

---

## References

- [Design Document § 7 (Agent Spine Architecture)](./design/2026-04-14-trax-io-inventory-optimizer-design.md)
- [ADR-0005 (Deterministic Agent Spine Core)](./adr/2026-06-27-0005-deterministic-agent-spine-core.md)
- [Full Feature Guide § 3 (Orchestration Example)](./guides-src/04-full-feature-guide.md)
- [Agent-Spine Implementation Plan](./plans/2026-04-14-agent-spine-implementation-plan.md)

