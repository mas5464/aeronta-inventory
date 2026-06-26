# trax-io-reco — Recommendation Engine (deterministic v1)

Turns eMRO feature data into ranked inventory-action recommendations — **Purchase, Transfer,
Reduce Stock, Sell, Adjust Min/Max** — each with evidence, an AOG risk level, a suggested
autonomy tier, and a deterministic confidence score. No LLM in the path; read-only (no eMRO
writes). Forward-compatible with the Agent Spine (#4) and Forecasting (#5) contracts.

- **Spec:** [docs/superpowers/specs/2026-04-17-trax-io-recommendation-engine-design.md](../../docs/superpowers/specs/2026-04-17-trax-io-recommendation-engine-design.md)
- **Plan:** [docs/superpowers/plans/2026-04-17-trax-io-recommendation-engine.md](../../docs/superpowers/plans/2026-04-17-trax-io-recommendation-engine.md)
- **ADR:** [docs/adr/2026-04-17-0004-deterministic-recommendation-layer.md](../../docs/adr/2026-04-17-0004-deterministic-recommendation-layer.md)

## Install / test / lint

```bash
cd services/recommendation-engine
uv sync --extra dev                       # core + test tooling
uv run --extra dev pytest                 # 121 tests (2 API tests skip without the api extra)
uv run --extra dev ruff check .

uv sync --extra dev --extra api           # add the optional HTTP API + test client
uv run --extra dev --extra api pytest     # 123 tests, no skips
```

The feature-store dependency is wired as a **non-editable** path install
(`[tool.uv.sources] { path = "../feature-store" }`); after editing the feature store, run
`uv sync --reinstall-package trax-io-feature-store`.

## CLI

```bash
uv run trax-io-reco run --data-file examples/seed.json --reporting-horizon 30
uv run trax-io-reco run --data-file examples/seed.json --type purchase --now 2026-04-17T09:00:00
```

The `--data-file` JSON (see [`src/trax_io_reco/data/demo_loader.py`](src/trax_io_reco/data/demo_loader.py)
for the full field list):

```json
{
  "tenant_id": "acme",
  "parts": [
    {"pn": "P-100", "location": "YYZ", "monthly_units": [20, 20, 20],
     "serviceable": 2, "lead_mean_days": 60, "current_policy": [5, 5, 2, 40],
     "tier": 3, "unit_cost": "400", "part_class": "expendable"}
  ]
}
```

The CLI prints a `RecommendationBatch` as JSON — the same contract the optional HTTP API returns.

## Dry run on REAL extract data (shadow-mode, no AWS/Oracle/Spark)

Point the CLI at a nightly-extract output directory (the 21 `<domain>.json` files + `manifest.json`)
instead of a synthetic seed file:

```bash
uv run trax-io-reco run --extract-dir examples/extract_sample --now 2026-04-17T09:00:00
```

`build_stores_from_extract` ([`data/extract_loader.py`](src/trax_io_reco/data/extract_loader.py))
applies the real transforms — column-maps for stock/policy/attributes/vendor, monthly demand
bucketing from the rotable/expendable transaction rows, lead-time derivation from closed-order
dates, open-orders and interchange graphs — and seeds the engine's stores. This is the **shadow-mode
dry run**: real eMRO data in, a judge-able recommendation batch out, zero cloud dependencies. The
transform logic here is the reference that promotes into the Feature-Store Glue jobs.

v1 simplifications: vendor economics collapse to one canonical vendor per part; AOG signal and
repair TAT remain empty stubs (no extract source); the essentiality→tier map is a tenant-overridable
default. `examples/extract_sample/` is a runnable sample produced by `tests/fixtures/extract_fixture.py`.

## HTTP API (optional, `api` extra)

`GET /v1/recommendations?tenant=&location=&type=&min_confidence=` and
`GET /v1/recommendations/{pn}/{location}`. `trax_io_reco.api.app.create_app(run_batch)` takes a
`(tenant_id, reporting_horizon) -> RecommendationBatch` provider; FastAPI is imported lazily so
the core install never needs it.

## Architecture

```
work-list (pn, location)
  → ContextAssembler → RegimeClassifier → DemandProjector (per-day rate + dist params)
  → NetPosition (window-parameterized, interchange rollup)
  → {AdjustMinMax · Purchase · Transfer · ReduceSell}
  → Arbitration → AOG risk scorer → confidence + ranking
  → RecommendationBatch
```

Data comes from the real `FeatureStoreClient` for the served groups (now including
`get_stock_position` / `get_current_policy`, promoted in Phase 2), and from an engine-owned
`InventoryStateProvider` for the three inputs the feature store does not yet model.

## Input sourcing (Phase 2)

| Input | Source | Status |
|---|---|---|
| On-hand stock position | feature-store `get_stock_position` (`stock_amount` #18) | ✅ promoted to FS #2 |
| Current ROP/EOQ/SS/Max | feature-store `get_current_policy` (`stock_level_upload` #19, alias-corrected) | ✅ promoted to FS #2 |
| Part description / attributes / criticality / vendor economics / demand / lead time / open orders / interchange | feature-store reads | ✅ FS-served |
| Scheduled/forward demand | `InventoryStateProvider` — sparse in v1 | stub → v2 causal forecasting |
| AOG signal/history | `InventoryStateProvider` — no extract domain yet | stub → new extract domain / event feed |
| Repair TAT | `InventoryStateProvider` — proxy from closed ROs | stub → derived feature |

The ML forecasting ensemble (#5), Bedrock/Strands runtime + Cedar enforcement (#4), eMRO
writeback (#6), and Planner UI (#7) remain in their own sub-plans. The engine *emits* the
suggested autonomy tier and guardrail flags; the Guardrail specialist (#4) enforces them.
