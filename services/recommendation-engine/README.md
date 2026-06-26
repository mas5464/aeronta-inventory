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

Data comes from the real `FeatureStoreClient` for the nine groups it serves, and from an
engine-owned `InventoryStateProvider` for the inputs the feature store does not yet model.

## v1 stubs (promotion paths)

| Input | v1 source | Promotes to |
|---|---|---|
| On-hand stock position | `InventoryStateProvider` (shape of `stock_amount` #18) | feature-store #2 `get_stock_position` |
| Current ROP/EOQ/SS/Max | `InventoryStateProvider` (`stock_level_upload` #19, alias-corrected) | feature-store #2 `get_current_policy` |
| Scheduled/forward demand | `InventoryStateProvider` — sparse in v1 | v2 causal forecasting |
| AOG signal/history | `InventoryStateProvider` — no extract domain yet | new extract domain / event feed |
| Repair TAT | `InventoryStateProvider` — proxy from closed ROs | new derived feature |
| Part description | real, from `part_attributes.description` (part_master #15) | — |

The ML forecasting ensemble (#5), Bedrock/Strands runtime + Cedar enforcement (#4), eMRO
writeback (#6), and Planner UI (#7) remain in their own sub-plans. The engine *emits* the
suggested autonomy tier and guardrail flags; the Guardrail specialist (#4) enforces them.
