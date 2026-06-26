# Trax IO — Recommendation Engine (deterministic v1)

**Design document / spec**
**Date:** 2026-04-17
**Owner:** Miguel Sosa, VP Head of Innovation, Trax
**Status:** Approved design — revised after adversarial review — ready for implementation planning
**Sub-project:** Proposed new sub-project **pending [ADR-0004](../../adr/) + a roadmap amendment** (see §3.1). Working location: `services/recommendation-engine/`.
**Authoritative parents:** [v1 design](../../design/2026-04-14-trax-io-inventory-optimizer-design.md) · [forecasting & policy plan](../../plans/2026-04-14-forecasting-policy-plan.md) · [feature-store plan](../../plans/2026-04-14-feature-store-plan.md) · [ExtractManifest contract](../../contracts/2026-04-17-extract-manifest-contract.md)

---

## 1. Summary

A deterministic, no-LLM **Recommendation Engine** that, for a supplied work-list of `(PN, Location)` keys, produces ranked inventory-action recommendations of five types — **Purchase, Transfer, Reduce Stock, Sell, Adjust Min/Max** — each with full evidence and provenance. Every recommendation is annotated with an **AOG risk level** (a cross-cutting score, not a sixth type).

The engine is built around one shared primitive — the **net position** per `(PN, Location)`. From that single computation, five pluggable recommenders derive their output, an explicit **arbitration** stage removes contradictions, an **AOG risk scorer** annotates, and a deterministic **ranker** orders the batch. The policy math (the **Adjust Min/Max** anchor) follows the locked design §5.4/§5.5/§6.2 and is forward-compatible with the Agent Spine / Forecasting contracts so it can be promoted unchanged when those land.

**In scope:** contracts, deterministic recommendation logic, demand projection (historical intensity + scheduled demand, as a per-day rate), net-position math, a minimal deterministic policy engine *including* the numeric compound-Poisson/NBD quantile path, the five recommenders, arbitration, AOG risk scoring, confidence scoring, ranking, a library facade, a `click` CLI, an optional thin FastAPI read API, and a full test suite (the eight required scenarios + invariants).

**Out of scope (stays in existing sub-plans):** ML forecasting ensemble → #5 · Bedrock/Strands runtime + Cedar enforcement → #4 · eMRO writeback → #6 · Planner UI → #7 · real Iceberg/DynamoDB feature backfill → #2.

---

## 2. What changed after recon + review (read this first)

This spec was reconned against the live code and then adversarially reviewed by four independent lenses (design fidelity, requirements completeness, internal consistency, deterministic-math buildability). All four returned *approve-with-fixes*; every finding is folded in below. The load-bearing resolutions:

### 2.1 The Feature Store cannot serve on-hand stock or current policy — **DECISION**
The shipped `FeatureStoreClient` exposes **10 point-lookup methods**, none of which returns current on-hand stock or the current `PN_INVENTORY_LEVEL` (ROP/EOQ/SS/Max). The data exists in the extract layer (`stock_amount` #18, `stock_level_upload` #19) but is not yet a read feature group. **Resolution:** read the inputs the feature store already serves through the real `FeatureStoreClient`; serve the gaps through an engine-owned `InventoryStateProvider` + `InMemoryInventoryState` stub (the project's established "ship-stub-in-consumer, promote-later" pattern). #2 is untouched.

### 2.2 No HTTP precedent in the repo — **DECISION**
The authoritative "API/UI response structure" is the pydantic `RecommendationBatch` (it serializes to the exact JSON the UI returns). Core ships framework-free: the `RecommendationService` library + a `click` CLI. A thin FastAPI read app is isolated behind an optional `api` extra so it never pollutes the core dependency set.

### 2.3 The engine suggests, the Guardrail enforces — boundary made explicit
The engine **implements** the §6.2 hard caps that are pure functions of a single recommendation (floors, shelf-life clamp, hazmat/tool 2× cap, open-order deferral). It does **not enforce** the cross-cutting rules (single-write 100% delta cap; active-AOG→Tier-A) — those belong to the Guardrail specialist (#4). For those, the engine **pre-populates a non-binding suggestion**: it sets `suggested_autonomy_tier` and appends `guardrail_flags` (e.g. `delta_gt_100pct`, `active_aog`), but the authoritative routing/clamp is the Guardrail's. So when AOG is active the engine *suggests* `ADVISOR` and flags `active_aog`; it does not claim to enforce Tier A.

### 2.4 Smaller recon/review findings folded in
- **Regime source.** Engine ships its own deterministic `RegimeClassifier` (24-mo event-count thresholds + ±20% hysteresis) producing the spine's `Regime`. (§6.1)
- **Forward-compatible contracts.** Mirror the spine shapes field-for-field *including enum member names* (`ForecastHorizon.DAYS_30…DAYS_180`). The engine's own `horizon_days` is a free positive int (protection periods like 45/90d are needed), deliberately decoupled from `ForecastHorizon`; the mirror exists only for promotion fidelity and is pinned in `test_contracts.py`. (§5.1)
- **`stock_level_upload` (#19) column transposition** (`PN`/`LOCATION` swapped) — the `current_policy` mapping corrects it; the upstream SQL is fixed separately (§11).
- **Logging:** stdlib `logging.getLogger("trax_io.reco.<area>")` — no structlog.

---

## 3. Scope, governance, and roadmap mapping

### 3.1 Governance — this is a conscious v1 re-scope (owner ratifies)
The locked roadmap freezes 10 sub-projects, and design §8 phases these capabilities: AOG→v3, Transfer/Reduce/Sell→v4, Purchase/sourcing→v5. Building deterministic precursors of all five **now** is a deliberate expansion of the locked Q1 v1 mission ("dynamic stock-level tuning"). The owner has approved this scope verbally; this spec records it but does **not** unilaterally renumber the register. **Action:** ratify via **ADR-0004 (deterministic recommendation layer in v1)** + a roadmap amendment that assigns the sub-project id. Until then the spec refers to it by name, not number.

### 3.2 What v1 delivers per type
| Type | Locked phase | Deterministic v1 delivery |
|---|---|---|
| **Adjust Min/Max** | v1 (core) | Full policy engine: regime-routed `(S−1,S)`/`(s,S)`/`(R,Q)` + §5.5 service levels + §6.2 caps. |
| **Purchase** | v1 implicit / v5 explicit | Net position over the **protection period** < 0 and open orders don't cover → buy qty. |
| **Transfer** | v4 | Shorting location + sibling holding excess (directed interchange / shared main warehouse), transfer beats purchase. |
| **Reduce / Sell** | v4 | On-hand ≫ Max, ~0 usage, high value (or shelf-life expiring). |
| **AOG risk** | v3 | Cross-cutting scorer; v1 lacks a real AOG-history extract (stubbed, §10) — risk is driven by criticality × shortage × recovery time, with AOG history added when a domain lands. |

All eight required test scenarios are deterministic outcomes over the net position.

---

## 4. Architecture

### 4.1 The pipeline (deterministic now, agent-shaped for later)

Mirrors the future Strands Supervisor dispatch so each recommender later becomes a specialist tool with no re-architecture. No LLM anywhere.

```
work-list of (pn, location)
  │  ContextAssembler        → PartLocationContext        (FeatureStoreClient + InventoryStateProvider + TenantPolicyConfig)
  │  RegimeClassifier         → Regime
  │  DemandProjector          → DemandProjection          (per-DAY rate: mean/day, std/day, dist params, by_aircraft, by_task)
  │  NetPositionCalculator    → NetPosition               (group rollup + per-location; window-parameterized)
  │  Recommenders (run all, each emits 0..n):
  │      AdjustMinMax · Purchase · Transfer · ReduceSell
  │  Arbitration              → contradiction-free, residual-corrected set        ◀── NEW stage
  │  AogRiskScorer            → annotate (risk level, suggested tier, expedite)
  │  Confidence + Ranker      → confidence_score; deterministic total order
  ▼
RecommendationBatch  (the API/UI response contract)
```

`NetPosition` is **window-parameterized**: the demand projection is a per-day rate, and each recommender requests the net position over the window that is correct for it (Purchase over the protection period = lead time + review; Adjust over the LTD window; ReduceSell over the basis window). One projection, many windows — no single fixed horizon drives every recommender.

### 4.2 Component responsibilities (one purpose each)

| Component | File | Responsibility |
|---|---|---|
| `ContextAssembler` | `data/assembler.py` | Build one `PartLocationContext` from all sources; populate `description` from part attributes. |
| `FeatureReader` | `data/feature_reader.py` | Typed wrapper over the **9 `FeatureStoreClient` reads** used (see §5.4); `FeatureStoreLookupError` → typed empty. |
| `InventoryStateProvider` / `InMemoryInventoryState` | `data/inventory_state.py` | Serve the gap inputs: stock_position, current_policy, scheduled_demand, aog_signal, repair_tat. v1 stub. |
| `RegimeClassifier` | `regime/classifier.py` | Deterministic regime + hysteresis. |
| `DemandProjector` | `demand/projection.py` | Per-day demand rate + distribution params + by-aircraft/by-task breakdown (`Protocol` so ML swaps in). |
| `NetPositionCalculator` | `position/net_position.py` | `available + receipts(window) − demand(window)`; interchange rollup + apportionment. |
| `MiniPolicyEngine` | `policy/mini_engine.py` | Regime dispatch → `PolicyRecommendation`; numeric quantile path for non-normal regimes. |
| 5 recommenders | `recommenders/*.py` | Each derives 0..n `Recommendation`. |
| `Arbitrator` | `arbitration.py` | Remove contradictions; transfer-before-purchase residual; reduce/sell vs buy mutual exclusion. |
| `AogRiskScorer` | `risk/aog.py` | Risk level (part-class-correct recovery time) + suggested tier + expedite. |
| `confidence` | `confidence.py` | Deterministic confidence_score from data completeness/provenance. |
| `Ranker` | `ranking.py` | Deterministic total order. |
| `RecommendationService` | `service.py` | Orchestrate the pipeline over a work-list. |
| `cli.py` / `api/app.py` | — | Emit `RecommendationBatch` JSON (CLI) / HTTP (optional `api` extra). |

> **Test doubles:** tests seed the **real `InMemoryFeatureStore`** from `trax_io_feature_store` (via its `.seed(tenant_id, bucket, key, value)` method — keys are the lookup args in method-signature order) plus the engine's own `InMemoryInventoryState`. There is no engine-side feature-store stub.

---

## 5. Contracts (`src/trax_io_reco/contracts/`)

Two layers: **(a) forward-compatible mirrors** of spine/forecasting contracts (promote to `trax_io.contracts.*` unchanged), and **(b) engine-owned** recommendation/context contracts. All pydantic models use `ConfigDict(frozen=True, extra="forbid")`; money is `Decimal`; quantities `NonNegativeInt`.

### 5.1 Forward-compatible mirrors (`contracts/enums.py`, `contracts/policy.py`)
Field names, types, **and enum member names** match the spine exactly (recon-verified):
- `Regime(StrEnum)` = `ULTRA_RARE | INTERMITTENT | MODERATE | HIGH_VOLUME`
- `CanonicalCriticality(IntEnum)` = `TIER_1=1 … TIER_5=5` (ordered)
- `PolicyKind(StrEnum)` = `BASE_STOCK="base_stock" | S_S="s_S" | R_Q="R_Q"`
- `AutonomyTier(IntEnum)` = `ADVISOR=1 | BOUNDED=2 | AUTONOMOUS=3`
- `ForecastHorizon(IntEnum)` = `DAYS_30=30 | DAYS_60=60 | DAYS_90=90 | DAYS_180=180` (named members preserved; mirror only — see note)
- `PolicyRecommendation` — frozen; `tenant_id, pn, location, rop:int≥0, eoq:int≥0, safety_stock:int≥0, max_stock:int≥0, policy_kind:PolicyKind, service_level_target:float∈[0,1]=0.95, provenance_id:str, model_id:str="stub"`; **model_validator enforces `rop ≥ safety_stock` and `max_stock ≥ rop + eoq`**. The engine sets `model_id="deterministic-v1"` *at construction*, leaving the contract default (`"stub"`) untouched.

> **Horizon note:** the engine's own `horizon_days` fields are **free positive ints** (protection periods vary per part), intentionally decoupled from `ForecastHorizon`. The mirror exists solely so the forecasting contract promotes cleanly; `test_contracts.py` pins its name/value map.

### 5.2 Recommendation contracts (`contracts/recommendation.py`)
```text
EvidenceKind(StrEnum) = WORK_ORDER | MAINTENANCE_EVENT | TASK_CARD | OPEN_ORDER | DEMAND_HISTORY | DONOR_STOCK | SHELF_LIFE | AOG_EVENT
AogRiskLevel(IntEnum) = NONE=0 | LOW=1 | MEDIUM=2 | HIGH=3 | CRITICAL=4
RecommendationType(StrEnum) = PURCHASE | TRANSFER | REDUCE_STOCK | SELL | ADJUST_MIN_MAX

Evidence: kind, ref_id, detail, as_of: date|None

Recommendation (frozen):
  recommendation_id: str                  # str(ULID())
  tenant_id, type, part_number
  description: str                         # REQUIRED, non-empty — from part attributes (§5.3)
  current_location, recommended_location: str|None   # recommended_location set iff TRANSFER
  current_stock: int
  projected_demand: float                  # over the window actually used (horizon_days)
  shortage_quantity: float                 # >= 0
  recommended_quantity: float              # buy/transfer/reduce qty; for ADJUST = proposed Max
  estimated_cost_impact: Decimal           # signed $: outlay (+) / holding released (−)
  aog_risk_level: AogRiskLevel
  reason: str                              # non-empty
  supporting_evidence: tuple[Evidence,...] # non-empty
  confidence_score: float                  # [0,1], deterministic (§7.9)
  horizon_days: int                        # the window THIS rec used (protection period for Purchase, etc.)
  suggested_autonomy_tier: AutonomyTier    # non-binding suggestion (§7.8)
  guardrail_flags: tuple[str,...]
  generated_at: datetime
  input_snapshot_hash: str                 # canonical sha256 (§7.9) — excludes volatile fields
  policy: PolicyRecommendation|None        # ADJUST only — proposed (rop,eoq,ss,max)
  current_policy: CurrentPolicy|None       # ADJUST only — existing values for the diff

RecommendationBatch (frozen):
  tenant_id, generated_at, reporting_horizon_days: int   # default 30; reporting window only
  recommendations: tuple[Recommendation,...]             # ranked, contradiction-free
  skipped: tuple[SkippedKey,...]                         # (pn, location, reason)
  summary: BatchSummary                                  # counts by type + by aog level
```

### 5.3 Context contracts (`contracts/context.py`) — every sub-field defined once
Source columns in parentheses; FS = feature-store schema field, IP = `InventoryStateProvider` stub.

```text
StockPosition (IP ← stock_amount #18):
  on_hand, serviceable, unserviceable_in_repair, allocated_reserved, rental, loan   (NonNegativeInt)

CurrentPolicy (IP ← stock_level_upload #19, alias-corrected):
  rop, eoq, safety_stock, max_stock: NonNegativeInt;  replenishment_lead_days: NonNegativeFloat

VendorEconomics (FS get_vendor_economics):
  unit_cost: Decimal; market_value_unit_cost, average_cost, kit_cost, repair_cost_24mo_avg: Decimal|None;
  minimum_order_qty: NonNegativeInt; currency: str            # MinOQ lives HERE

PartAttributes (FS get_part_attributes):
  description: str|None; ata_chapter: str|None;
  part_class: Literal["rotable","repairable","expendable","consumable"]|None;   # drives AOG recovery-time choice
  shelf_life_days: NonNegativeInt|None; hazardous_material: bool; tool_control_item: bool;
  fleet_effectivity_tail_count: NonNegativeInt|None

Criticality (FS get_criticality):  raw_essentiality_code: str; canonical_tier: 1..5

LeadTime (FS get_lead_time_distribution):
  vendor: str; condition; promised_lead_days, realized_mean_days, realized_p50/p90/p99_days: NonNegativeFloat;
  n_observations: NonNegativeInt                              # procurement lead-time distribution

LocationGraph (FS get_location_graph):
  location; related_main_warehouse: str|None; role: Literal["main","outstation"]; children: list[str]

OpenOrders (FS get_open_orders_snapshot):
  snapshot_at: datetime; orders: list[OpenOrder(order_id, order_type∈{PO,RO}, vendor, qty_open, expected_rcv_date)]

InterchangeGroup (FS get_interchangeable_graph):
  group_id; members: list[str]; edges: list[(from_pn, to_pn, one_way: bool)]   # DIRECTED

ScheduledDemandItem (IP ← events #4 + part_kit_bom #13; SPARSE in v1, §10):
  due_date: date; qty: NonNegativeInt; source_ref: str; source_kind: EvidenceKind; ac_type: str|None
AogSignal (IP ← no extract domain; v1 stub):
  active: bool; last_event_date: date|None; events_24mo: int; last_shortage_at: datetime|None   # 72h window field
RepairTat (IP ← derived from closed ROs #7; v1 stub):
  mean_days, p90_days: NonNegativeFloat; n_observations: NonNegativeInt
TenantPolicyConfig (onboarding config — NOT provider-served):
  service_level_by_tier: dict[int,float] (defaults §5.5); holding_cost_rate, ordering_cost: float; currency

DemandProjection:
  mean_per_day, std_per_day: float                           # RATE — scaled to any window by callers
  dist_kind: Literal["NORMAL","COMPOUND_POISSON","NBD","EMPIRICAL"]
  dist_params: dict[str,float]                               # e.g. {lambda, clump_p} or {mean,var}
  historical_component, scheduled_component: float           # per-day
  by_aircraft: dict[str,float]; by_task: dict[str,float]     # scheduled demand itemized (sparse v1)
  basis_window_days: int

NetPosition (computed for a requested window W):
  pn, location, group_id, window_days: int
  available: float                                           # = serviceable − allocated_reserved  (see §5.5)
  expected_receipts_in_window: float
  projected_demand: float                                    # = demand_rate · W (Normal) or window-correct quantile
  net: float                                                 # = available + expected_receipts − projected_demand
  shortage: float                                            # = max(0, −net)

PartLocationContext:
  tenant_id, pn, location, stock_position, current_policy, vendor_economics, part_attributes,
  criticality, lead_time, location_graph, open_orders, interchange_group, demand_history,
  scheduled_demand: list[ScheduledDemandItem], aog_signal, repair_tat, tenant_policy_config
```

### 5.4 The FeatureStoreClient methods FeatureReader wraps (9 of 10)
`get_demand_history`, `get_causal_utilization`, `get_lead_time_distribution`, `get_vendor_economics`, `get_part_attributes`, `get_criticality`, `get_interchangeable_graph`, `get_location_graph`, `get_open_orders_snapshot`. (`get_wash_rate_history` is available but unused in deterministic v1 — wash-rate trend feeds the ML forecaster, #5.) On-hand stock and current policy are **not** here — they come from `InventoryStateProvider` (§2.1).

### 5.5 `available` and receipts — pinned formula (no double-counting)
- `available = max(0, serviceable − allocated_reserved)`. **Excluded** from available: `unserviceable_in_repair` (not dispatchable), `rental`, `loan` (borrowed liabilities). `on_hand` is the gross figure for display only; dispatchable stock is `serviceable`.
- `expected_receipts_in_window = Σ(open_orders.qty_open where expected_rcv_date ≤ as_of + W) + Σ(repair returns: units in unserviceable_in_repair with projected RO-close ≤ W, projected via RepairTat)`. **The two sources are disjoint** (open PO/RO lines vs in-house repair units never reference the same physical unit). Invariant test asserts a unit cannot appear in both `open_orders` and `unserviceable_in_repair`.

---

## 6. Deterministic policy engine (the Adjust Min/Max anchor)

### 6.1 Regime classification (`regime/classifier.py`)
From trailing-24-month events (`removals + issues`): `<6 → ULTRA_RARE · 6–24 → INTERMITTENT · 25–200 → MODERATE · >200 → HIGH_VOLUME`; ±20% hysteresis around each threshold when a prior regime is supplied; new PN (<90 d history) → `ULTRA_RARE`.

### 6.2 Policy dispatch (`policy/mini_engine.py`)
```text
if regime == ULTRA_RARE and criticality <= TIER_2:  compute_base_stock(...)   → BASE_STOCK
elif regime == ULTRA_RARE:                           compute_base_stock(...)   → BASE_STOCK   ◀ tier 3–5 routed to base-stock (see note)
elif regime == INTERMITTENT:                         compute_s_S(...)          → S_S
else:                                                compute_R_Q(...)          → R_Q
```
- **Base-stock `(S−1,S)`** — smallest integer `S` s.t. `P(LTD > S) ≤ 1 − target`, evaluated against the LTD distribution (§6.4).
- **`(s,S)`** — Wilson `EOQ = √(2·D·K/h)` adjusted for the LTD distribution; `s = ROP = LTD_mean + SS`; `S = ROP + EOQ`; `SS` from the §6.4 quantile (compound-Poisson/NBD tail), not a normal approximation.
- **`(R,Q)`** — periodic review; `Q = EOQ` floored at `MinOQ`; `SS = z_α · σ_LTD` (normal fast-path is acceptable for moderate/high-volume).
- **Service-level target** from `TenantPolicyConfig.service_level_by_tier` (defaults §5.5: 99.5/98/95/92/90).

> **Two deliberate v1 deviations from design §5.4, stated explicitly (not "exact"):**
> (1) §5.4 says ultra-rare tier 1–2 base-stock targets are "driven by the AOG cost model"; the AOG cost model is a v3 item, so v1 approximates it with the §5.5 tier-1 fill-rate (99.5%). (2) §5.4 specifies base-stock only for tier 1–2; rather than let sparse ultra-rare tier 3–5 fall into a degenerate `(R,Q)` (D≈0 ⇒ EOQ→MinOQ, meaningless σ), v1 routes **all** ultra-rare to base-stock. Both are flagged for revisit when #4/#5 land.

### 6.3 Hard constraints (`policy/constraints.py`) — §6.2, applied after the math, may only tighten
Floors (`SS≥0, ROP≥SS, Max≥ROP+EOQ, EOQ≥MinOQ`; also enforced by the `PolicyRecommendation` validator) · shelf-life clamp `Max × avg_daily_demand ≤ 0.6 × shelf_life_days` (avg_daily_demand from `DemandProjection.mean_per_day`) · hazmat/tool `Max ≤ 2 × current_max` · open-order deferral (if `available + expected_receipts > proposed Max`, emit no Adjust this cycle, flag `open_order_deferral`).

**Constraint-violation control flow:** if tightening would violate a floor, the engine **does not construct an invalid `PolicyRecommendation`** (that would raise in the frozen validator). It catches the violation, records the key in `batch.skipped` with reason `policy_constraint_violation:<detail>` and `suggested_autonomy_tier=ADVISOR`, and emits no ADJUST for that key. Tested.

### 6.4 LTD distribution + quantiles (`policy/service_level.py`, `policy/lead_time.py`)
The demand→policy chain is closed deterministically for **every** regime:
- `DemandProjector` emits `dist_kind` + `dist_params` per regime, fit by method-of-moments (no ML): ULTRA_RARE/INTERMITTENT → `COMPOUND_POISSON` (`λ = events / basis_days`, clump parameter from mean demand-per-event) or `NBD`; MODERATE/HIGH_VOLUME → `NORMAL` (mean/var).
- **LTD convolution** (`lead_time.py`): NORMAL fast-path (sum of means, sum of variances + cross-term); **numeric slow-path implemented now** for compound-Poisson/NBD demand convolved with the lead-time distribution → an LTD PMF.
- **Quantile inversion** (`service_level.py`): `z_for_fill_rate = norm.ppf`; `safety_stock_normal`; and `ltd_quantile(dist, p)` over the PMF for base-stock `S` and intermittent `SS`. A unit test pins `S` for a known compound-Poisson case so the chain is provably closed.

Dependency: `scipy>=1.13` (already the forecasting stack's choice).

### 6.5 Lead-time precedence
Three lead-time notions, explicit precedence:
- **Protection period** (Purchase window, LTD/safety-stock): `lead_time.realized_mean_days` if `n_observations>0`, else `lead_time.promised_lead_days`, else `current_policy.replenishment_lead_days`, else 14 d.
- **`(R,Q)` review period**: vendor review cycle (`lead_time` promised days) → 14 d fallback.
These are recorded in provenance so an auditor sees which value drove each number.

---

## 7. Recommenders, arbitration, scoring

Each recommender implements `propose(context, net_position_fn, regime) -> list[Recommendation]`, where `net_position_fn(window_days)` yields the window-correct `NetPosition` (§4.1).

### 7.1 `AdjustMinMaxRecommender`
Runs `MiniPolicyEngine`, diffs proposed `(ROP,EOQ,SS,Max)` vs `current_policy` over the LTD window. Emits `ADJUST_MIN_MAX` when any value moves beyond the **materiality epsilon** (default: relative change `> 0.05`, strict). Sets `guardrail_flags += "delta_gt_100pct"` when a single-write delta exceeds 100% (engine flags; Guardrail caps). `recommended_quantity` = proposed Max. Evidence: `DEMAND_HISTORY` window + binding constraints. `horizon_days` = LTD window.

### 7.2 `PurchaseRecommender`
Computes `NetPosition` over the **protection period** `W = max(protection_period_days, reporting_horizon_days)` (§6.5). Fires when `net < 0` and open orders don't cover: `buy_qty = ceil(shortage + safety_stock − on_order)`, floored at `MinOQ`. **Suppressed** when open POs covering ≤ W already meet projected demand (scenario 6). `estimated_cost_impact = buy_qty × unit_cost`. `horizon_days = W`. Evidence: `OPEN_ORDER` shortfall + the driving demand.

### 7.3 `TransferRecommender`
Fires when this location has `shortage > 0` and a sibling location holds excess (`serviceable − Max > 0`) that is a **valid directed substitution** — same `group_id` with a permitted edge direction (donor PN → receiver PN; one-way edges honored) and/or sharing `related_main_warehouse`. `recommended_location` = donor; `recommended_quantity = min(shortage, donor_excess)`. `estimated_cost_impact` = avoided purchase outlay. Evidence: `DONOR_STOCK` + this location's shortage.

### 7.4 `ReduceSellRecommender`
Fires when `serviceable > 1.5 × Max` (strict) AND trailing usage over the basis window `== 0` AND high unit value (`unit_cost ≥ tenant high_value_threshold`); shelf-life-expiring stock also routes here. Boundary: **`SELL`** iff usage `== 0` over basis window AND no `scheduled_demand` within horizon AND `unit_cost ≥ threshold`; **else `REDUCE_STOCK`**. `recommended_quantity` = excess units; `estimated_cost_impact` = holding released (negative). Evidence: zero-usage window, on-hand vs Max, `SHELF_LIFE` where relevant.

### 7.5 `Arbitrator` (`arbitration.py`) — NEW deterministic stage
Runs after all recommenders, before scoring. Guarantees a contradiction-free set per `(pn, location)`:
1. **Transfer before Purchase.** If both fire, keep Transfer; recompute Purchase against the **post-transfer** position and emit Purchase only for residual shortage (`buy_qty` recomputed; dropped if residual ≤ 0). The "transfer beats purchase" predicate is concrete: prefer transfer iff `transfer_lead_days < purchase_lead_days` OR (`equal lead` AND `transfer_cost ≤ purchase_cost`); ties → transfer.
2. **No excess + shortage on the same key.** A key with `net < 0` cannot also emit `REDUCE_STOCK`/`SELL`; suppress the excess rec (shortage wins). Reconciliation uses **group-rolled net for Adjust/Purchase "no over-buy"** and **per-location net for Transfer/location-scoped** recs.
3. Deterministic throughout (no dict/iteration-order dependence).

### 7.6 Interchange rollup + apportionment (`position/net_position.py`)
Per design §4.3 / forecasting Task 34: sum demand across **two-way** group members (honoring one-way edges as directed), run the policy on the aggregate, then apportion `(ROP,EOQ,SS,Max)` back to each PN **proportional to trailing-12-month consumption**. Group-level net feeds Adjust/Purchase ("no over-buy", scenario 5); per-location net feeds Transfer + location-scoping (scenario 7).

### 7.7 `AogRiskScorer` (`risk/aog.py`)
Runs over every surviving recommendation. **Recovery time is part-class-correct:** `recovery_time = repair_tat.p90_days` if `part_class ∈ {rotable, repairable}` and a repair loop exists, else procurement `protection_period_days`. Score from `criticality × shortage severity × recovery_time × recent AOG history`; maps to `AogRiskLevel`. When `repair_tat`/`aog_signal` are empty stubs it degrades gracefully (defined level from the available factors, never silent `NONE`) and lowers `confidence_score`. Side effects: active AOG (or `last_shortage_at` within 72 h) → `suggested_autonomy_tier=ADVISOR` + `guardrail_flags += "active_aog"` (suggestion, §2.3); `HIGH`/`CRITICAL` Purchase/Transfer annotated **expedite** in `reason`.

### 7.8 Autonomy-tier suggestion (normative thresholds)
The engine **suggests** (does not enforce) per §6.1 defaults, now stated normatively: **Tier A/ADVISOR** if criticality `TIER_1`, or `unit_cost ≥ $10K`, or single-write delta `> 25%`, or active AOG; **Tier C/AUTONOMOUS** if criticality `TIER_4–5` and `unit_cost < $500` and delta within `±40%`; **Tier B/BOUNDED** otherwise. (The §5.5 "max out-of-band" column and these §6.1 bands are not reconciled in the locked docs; authoritative routing is the Guardrail's — §11.)

### 7.9 Confidence + ranking (`confidence.py`, `ranking.py`)
- **`confidence_score`** — deterministic, in `[0,1]`: the product of component scores — demand-history sufficiency (`min(1, events / regime_threshold)`), input-provenance (`1.0` if the driving inputs are real feature-store reads; penalty when AOG/RepairTat/scheduled_demand are `InMemoryInventoryState` stubs or empty), constraint-binding penalty, and regime-fit (hysteresis stability). Exact weights specified in the plan; a unit test asserts confidence is **strictly lower** when AOG/RepairTat/scheduled_demand are empty stubs than when real. This makes the `min_confidence` API filter meaningful.
- **`Ranker`** — sort key, all descending then explicit tie-breaks for a **total order**: `score = criticality_weight × cost × |delta| × (1 + aog_risk_level)` computed in `Decimal`; ties broken by `criticality asc → part_number asc → RecommendationType enum order → current_location asc`. Independent of work-list/dict iteration order.
- **`input_snapshot_hash`** — canonical sha256 over the assembled context with **sorted keys, volatile fields excluded** (`generated_at`, `recommendation_id`). A determinism test runs a batch twice and asserts byte-identical output modulo ids.

All thresholds use explicit strictness (`>`, `≥`) and a defined epsilon for the 5% materiality test, so boundary values are stable across runs/platforms.

---

## 8. Interfaces
- **Library:** `RecommendationService(feature_store, inventory_state, config).run(tenant, keys, reporting_horizon_days=30) -> RecommendationBatch`.
- **CLI** (`cli.py`, `click`): `trax-io-reco run --tenant <id> --keys-file <path> [--reporting-horizon 30] [--type ...]` → prints `RecommendationBatch` JSON. `--reporting-horizon` accepts any positive int (per §5.1 note).
- **HTTP (optional, `api` extra):** thin FastAPI read app — `GET /v1/recommendations?tenant=&location=&type=&min_confidence=` and `GET /v1/recommendations/{pn}/{location}`. Read-only; same `RecommendationBatch`.

---

## 9. Testing
Flat `tests/` mirroring source subpackages; plain `pytest`; fixtures in `tests/fixtures/`. Run `uv run --extra dev pytest`.

### 9.1 The eight required acceptance scenarios (`tests/test_eight_scenarios.py`)
Each seeds the real `InMemoryFeatureStore` + `InMemoryInventoryState`, runs the full service, asserts type + fields. **Every scenario asserts `description` is non-empty.**

| # | Scenario | Single deterministic assertion |
|---|---|---|
| 1 | Demand exceeds stock (long-lead part) | exactly one `PURCHASE`; `shortage_quantity` correct over the **protection period** (lead=90 d > reporting 30 d). |
| 2 | Transfer better than purchase | exactly one `TRANSFER`, `recommended_location`=donor; **no** `PURCHASE` for the key. |
| 3 | High-value unused inventory | `REDUCE_STOCK` or `SELL` per the §7.4 boundary (fixture pins which); negative `estimated_cost_impact`. |
| 4 | Min/max adjustment | `ADJUST_MIN_MAX` with proposed `(ROP,EOQ,SS,Max)` ≠ current. |
| 5 | Interchangeable part (one-way edge) | demand/stock rolled to group; apportioned; **no over-buy**; substitution direction honored. |
| 6 | Open PO covers future demand | **no recommendation of type `PURCHASE`** for the key (single outcome; key may still yield ADJUST). |
| 7 | Location-specific shortage | recommendation scoped to the shorting location only. |
| 8 | Long TAT creates AOG risk (rotable) | `aog_risk_level ≥ HIGH`; `suggested_autonomy_tier=ADVISOR`; expedite in `reason`; RepairTat seeded so the TAT path runs. |

### 9.2 Unit tests
One per module, incl.: `test_service_level.py` (z/SS + **compound-Poisson `S` pin**), `test_lead_time.py` (normal + numeric convolution), `test_regime_classifier.py`, `test_mini_engine.py` (dispatch incl. ultra-rare tier 3–5 → base-stock; constraint-violation → `skipped`), `test_constraints.py`, `test_net_position.py` (**available formula; receipt outside window; no unit in both open_orders & in-repair**; rollup/apportion), `test_demand_projection.py` (per-day rate, dist params, by_aircraft/by_task), one per recommender, `test_arbitration.py` (**no contradictory pair on one key; transfer-before-purchase residual**), `test_aog.py` (**rotable→TAT, expendable→lead_days; graceful stub degradation**), `test_confidence.py` (**stub < real**), `test_ranking.py` (total order), `test_service.py`.

### 9.3 Property / invariant tests (`tests/test_invariants.py`)
Net-position identity; `shortage ≥ 0`; every `Recommendation` has non-empty `description`, `reason`, `supporting_evidence`, `confidence ∈ [0,1]`; `PolicyRecommendation` floors hold; ranking is a total order; **determinism** (same context → byte-identical batch modulo ids); no `(REDUCE_STOCK|SELL)` co-emitted with `(PURCHASE|TRANSFER)` on one key.

### 9.4 Contract pin (`tests/test_contracts.py`)
Mirror shapes match the spine plan field-for-field, **including `ForecastHorizon` member names/values** and the `PolicyRecommendation` validator + default.

---

## 10. Explicit v1 stubs & assumptions (recon-derived gaps)
Provider-served stubs (behind `InventoryStateProvider`), each with a promotion path:

| Input | v1 source | Promotes to |
|---|---|---|
| On-hand stock position | provider stub (shape of `stock_amount` #18) | feature-store #2 `get_stock_position` |
| Current ROP/EOQ/SS/Max | provider stub (`stock_level_upload` #19, alias-corrected) | feature-store #2 `get_current_policy` |
| Scheduled/forward demand (+ by_aircraft/by_task) | provider stub; **sparse** in v1 (`events` #4 filtered to `DUE_DATE ≤ as_of`, no per-task qty; `part_kit_bom` #13 static) | v2 causal forecasting (forward flight plans) |
| Task-card evidence | populated from `ScheduledDemandItem.source_ref` when present (sparse); primary evidence is `WORK_ORDER`/`MAINTENANCE_EVENT`/`DEMAND_HISTORY`/`OPEN_ORDER` | enrich feature-store `DemandObservation` with task_card/WO, or surface event-publisher `task_card` |
| AOG signal/history | provider stub — **no extract domain exists** | new extract domain (`DEFECT_REPORT`/WO priority) or event feed |
| Repair TAT distribution | provider stub — proxy from closed RO dates #7 | new derived feature / extract |
| `description` | **real**, from `part_attributes.description` (part_master #15 nomenclature) | — |

Configuration (NOT provider-served): `TenantPolicyConfig` (service-level targets, holding-cost rate, ordering cost) is injected as onboarding config. Every *provider* stub sits behind `InventoryStateProvider`, so promotion is an implementation swap with zero recommender change.

---

## 11. Risks & follow-ups
- **`stock_level_upload` (#19) alias transposition** is a real upstream defect (`PN`/`LOCATION` swapped); the `current_policy` mapping corrects it, and the SQL is fixed separately (tracked task).
- **Forward demand is weak in v1** (sparse scheduled-demand source); the scheduled component and by_aircraft/by_task breakdowns are conservative/sparse until v2. Disclosed; the requirement "projected demand by aircraft/task/date-range" is **structurally satisfied** (the contract carries the dimensions) but **data-PARTIAL** in v1.
- **AOG risk in v1 excludes real AOG-event history** (stubbed) — driven by criticality × shortage × recovery time until an AOG domain lands. Noted on the output field.
- **Tier-band reconciliation.** §5.5 out-of-band deltas and §6.1 tier-selection deltas are unreconciled in the locked docs; the engine emits the §7.8 suggestion + raw delta and leaves authoritative routing to the Guardrail spec (#4).
- **No population enumeration** in the feature-store contract; the engine consumes an externally-supplied work-list (the nightly orchestrator's job, out of scope).

---

## 12. File structure
```
services/recommendation-engine/
├── pyproject.toml            # trax-io-reco; deps: pydantic>=2.7, scipy>=1.13, python-ulid>=3, trax-io-feature-store (path);
│                             #   extras: dev=[pytest>=8.2,ruff>=0.4], api=[fastapi,uvicorn]
├── uv.lock · README.md
├── src/trax_io_reco/
│   ├── __init__.py
│   ├── contracts/{__init__,enums,policy,recommendation,context}.py
│   ├── data/{__init__,feature_reader,inventory_state,assembler}.py     # inventory_state.py = InventoryStateProvider + InMemoryInventoryState
│   ├── regime/{__init__,classifier}.py
│   ├── demand/{__init__,projection}.py
│   ├── position/{__init__,net_position}.py
│   ├── policy/{__init__,service_level,lead_time,base_stock,s_S,R_Q,constraints,mini_engine}.py
│   ├── recommenders/{__init__,base,adjust_min_max,purchase,transfer,reduce_sell}.py
│   ├── arbitration.py
│   ├── risk/{__init__,aog}.py
│   ├── confidence.py
│   ├── ranking.py
│   ├── service.py
│   ├── cli.py
│   └── api/{__init__,app}.py   # optional; imported only when `api` extra installed
└── tests/
    ├── __init__.py · fixtures/{__init__,scenarios}.py
    ├── test_service_level.py · test_lead_time.py · test_regime_classifier.py
    ├── test_mini_engine.py · test_constraints.py · test_net_position.py · test_demand_projection.py
    ├── test_adjust_min_max.py · test_purchase.py · test_transfer.py · test_reduce_sell.py
    ├── test_arbitration.py · test_aog.py · test_confidence.py · test_ranking.py · test_service.py
    ├── test_eight_scenarios.py · test_invariants.py · test_contracts.py
```

---

## 13. Conventions (matched to existing packages)
Python ≥3.12 · src layout · hatchling · `from __future__ import annotations` everywhere · absolute imports · pydantic v2 `ConfigDict(frozen=True, extra="forbid")` · `Decimal` money · `from ulid import ULID; str(ULID())` · stdlib `logging.getLogger("trax_io.reco.<area>")` (no structlog) · ruff `line-length=100`, `lint.select=["E","F","I","B","UP","N","SIM"]` · no mypy · `[project.optional-dependencies] dev` (not PEP 735 groups) · `[tool.pytest.ini_options] testpaths=["tests"] pythonpath=["src"]` · test command `uv run --extra dev pytest`.
