# Trax IO — Forecasting #5, Slice A: Classical Intermittent Forecasting — Design

**Date:** 2026-06-27
**Sub-project:** #5 Forecasting & Policy Engine (slice A of N)
**Status:** Design — approved in brainstorm, pending spec review → writing-plans
**Builds on:** #11 Recommendation Engine (the `DemandProjector` Protocol + `DemandProjection` contract + the deterministic `HistoricalScheduledProjector`). Authoritative sub-plan: [docs/plans/2026-04-14-forecasting-policy-plan.md](../../plans/2026-04-14-forecasting-policy-plan.md).

---

## 1. Context & decomposition

#5 ("Forecasting & Policy Engine") is large (45-task sub-plan). Two framings sharpen the scope:

- **The Policy Engine half is already built.** #11's deterministic `mini_engine` turns a `DemandProjection` + lead-time + service-level + §6.2 constraints into `(ROP, EOQ, SS, Max)`. So #5's net-new is the **forecasting** that replaces #11's deterministic `HistoricalScheduledProjector`.
- **The seam already exists.** #11 defines `DemandProjectorProtocol.project(*, context, regime) -> DemandProjection`; the policy engine consumes the `DemandProjection`. A #5 forecaster is simply a `DemandProjector` implementation.

Forecasting decomposes into slices: **A — classical intermittent (this spec)**; B — LightGBM for `moderate`/`high_volume`; C — ultra_rare empirical-Bayes compound-Poisson + Chronos challenger; D — SageMaker hosting + nightly scoring + auto-promotion gate.

**Slice A scope:** a regime-routed `StatisticalProjector` that applies statsforecast classical models (Croston / SBA / TSB, auto-selected) to the **`intermittent`** regime, producing a fitted `DemandProjection`; it **delegates every other regime** (`ultra_rare`, `moderate`, `high_volume`) to the deterministic projector. Plus a local backtest harness scoring the statistical vs deterministic projector. No SageMaker, no LLM, no AWS.

> Why intermittent only: `ultra_rare` (<6 events/24mo) lacks single-series signal for Croston — the design uses empirical-Bayes peer priors there (slice C). `moderate`/`high_volume` want LightGBM (slice B). `intermittent` (6–24 events/24mo, ~15–25% of catalog) is exactly the regime classical intermittent-demand methods were designed for and where they beat a flat historical average.

### Grounded facts (verified)
- Env is **Python 3.14**; `statsforecast` 2.0.x installs and runs here **with `scipy>=1.18` + `numba>=0.62` pinned** (the resolver otherwise selects an old scipy with no 3.14 wheel and fails a meson source build). `CrostonClassic`/`CrostonSBA`/`TSB` from `statsforecast.models` expose `forecast(y: np.ndarray, h: int) -> {"mean": np.ndarray}`; `mean[0]` is the constant per-period demand rate.
- #11 `HistoricalScheduledProjector.project`: `historical_per_day = total_demand / basis_window_days`; for `INTERMITTENT` it emits `dist_kind="COMPOUND_POISSON"`, `dist_params={"lambda": historical_per_day, "clump_p": 1.0}`, `std_per_day=sqrt(lambda)`, plus `scheduled_component` from `context.scheduled_demand`. **Slice A keeps this exact machinery, replacing only the historical-average rate with the fitted rate.**
- `DemandHistory.observations` are bucketed (`bucket ∈ {day, week, month}`, `period_start`, `removals`, `issues`). `_DAYS_PER_BUCKET = {"day":1, "week":7, "month":30.44}`.

---

## 2. Scope

### In scope (new package `services/forecasting/`, `trax_io_forecasting`)
1. `series.py` — turn a `DemandHistory` into a contiguous per-period numpy demand series + its bucket/days-per-period.
2. `classical.py` — thin statsforecast wrappers (`croston`/`sba`/`tsb` → per-period rate) + `select_model(series)` via the Syntetos-Boylan-Croston (SBC) classification (ADI, CV²).
3. `projector.py` — `StatisticalProjector(DemandProjector)`: `intermittent` → fitted `COMPOUND_POISSON` `DemandProjection`; other regimes → delegate to an injected fallback (`HistoricalScheduledProjector`).
4. `backtest.py` — hold-out backtest + **MASE** scoring; `compare(champion, challenger)` over a set of keys.
5. **One #11 enablement:** make `RecommendationService`'s projector injectable (`projector=None` → default `HistoricalScheduledProjector`).
6. Tests + adversarial review.

### Out of scope (deferred slices)
- LightGBM (`moderate`/`high_volume`); ultra_rare empirical-Bayes peer priors; Chronos/Moirai challenger.
- SageMaker training/endpoints; the Model Registry; nightly scoring + the 45-day auto-promotion gate.
- The `ForecastingAgent` Strands/AgentCore wrapper and the slim `ForecastDistribution` mirror (the policy path consumes `DemandProjection` directly).

---

## 3. Components

### 3.1 `series.py`
- **Purpose:** convert the engine's bucketed `DemandHistory` into the dense numeric series statsforecast wants.
- **Interface:** `to_period_series(history: DemandHistory) -> PeriodSeries` where `PeriodSeries` is a small frozen dataclass `{values: tuple[float, ...], bucket: str, days_per_period: float}`. `values` = `removals + issues` per observation, ordered by `period_start`, **gap-filled with zeros** for missing periods between the first and last observation (intermittent demand needs the zeros). Empty history → `values=()`.

### 3.2 `classical.py`
- **Purpose:** a uniform "fit → next-period rate" over statsforecast's classical models, plus model auto-selection.
- **Interface:**
  - `ClassicalModel` (StrEnum): `CROSTON`, `SBA`, `TSB`.
  - `forecast_rate(values: Sequence[float], model: ClassicalModel) -> float` — runs the statsforecast model `forecast(y, h=1)` and returns `mean[0]` (≥ 0; an all-zero or too-short series returns `0.0` without calling statsforecast).
  - `select_model(values: Sequence[float]) -> ClassicalModel` — SBC classification: compute `ADI = n_periods / n_nonzero` and `CV2 = var(nonzero) / mean(nonzero)**2`; `CV2 >= 0.49` (lumpy/erratic) → `SBA`; else if a high zero-fraction suggests obsolescence (`ADI >= 2.0`) → `TSB`; else → `CROSTON`. Deterministic.
- **Depends on:** `statsforecast.models` (lazy-imported inside `forecast_rate` so the module imports without the model JIT warming up).

### 3.3 `projector.py`
- **Purpose:** the `DemandProjector` #5 ships.
- **Interface:** `StatisticalProjector(fallback: DemandProjector | None = None, *, model: ClassicalModel | None = None, basis_window_days: int = 730)` implementing `project(*, context, regime) -> DemandProjection`.
  - **`regime is INTERMITTENT`:** `series = to_period_series(context.demand_history)`; `m = model or select_model(series.values)`; `rate_per_period = forecast_rate(series.values, m)`; `fitted_per_day = rate_per_period / series.days_per_period`. Build the `DemandProjection` with the **same machinery as the deterministic intermittent branch**: `dist_kind="COMPOUND_POISSON"`, `lambda = fitted_per_day`, `std_per_day = sqrt(lambda)`, `historical_component = fitted_per_day`, `scheduled_component` from `context.scheduled_demand` (reused), `mean_per_day = fitted_per_day + scheduled_per_day`, `basis_window_days`. The chosen model name is **not** stored on `DemandProjection` (its `dist_params` is `dict[str, float]` — no string field); it is logged for observability. Provenance is preserved regardless: the fitted `lambda` differs from the deterministic average, so the policy engine's existing `dist_kind|dist_params` provenance hash already distinguishes a fitted projection from a deterministic one.
  - **any other regime:** `return (fallback or HistoricalScheduledProjector(basis_window_days=…)).project(context=context, regime=regime)`.
- **Depends on:** #11's `DemandProjection`/`PartLocationContext`/`Regime`/`HistoricalScheduledProjector`/`DemandProjectorProtocol` (path dep `trax-io-reco`), `series`, `classical`.

### 3.4 `backtest.py`
- **Purpose:** the champion/challenger core — does the fitted forecast beat the historical average out-of-sample?
- **Interface:**
  - `mase(actual: Sequence[float], forecast: float, *, naive_scale: float) -> float` — mean absolute error of a constant `forecast` vs `actual`, scaled by the in-sample naive MAE (`naive_scale`); `naive_scale==0` → returns `inf` (degenerate series).
  - `backtest_key(series: PeriodSeries, projector_rate, *, holdout: int) -> float` — fit on `values[:-holdout]`, score the constant per-period rate against `values[-holdout:]` by MASE. `projector_rate` is a `Sequence[float] -> float` (so both the fitted model and the historical mean plug in).
  - `compare(series_by_key, *, holdout=6) -> BacktestReport{champion_mase, challenger_mase, n_keys, champion_wins}` — aggregates MASE across keys for the statistical champion (`forecast_rate` w/ `select_model`) vs the deterministic challenger (historical mean), reporting which wins on mean MASE.
- Pure/deterministic; no I/O.

### 3.5 #11 enablement
`RecommendationService.__init__` gains `projector: DemandProjectorProtocol | None = None`; `self._projector = projector or HistoricalScheduledProjector()`. Backward-compatible (default unchanged). This is the DI seam #5 plugs into — `RecommendationService(feature_store, inventory_state, projector=StatisticalProjector())`.

---

## 4. Data flow

```
context.demand_history (bucketed obs)
  → to_period_series → PeriodSeries(values, bucket, days_per_period)
  → select_model(values) → ClassicalModel
  → forecast_rate(values, model) [statsforecast Croston/SBA/TSB] → per-period rate
  → /days_per_period → fitted_per_day  (= COMPOUND_POISSON lambda)
  → DemandProjection (same dist machinery as #11, fitted mean)
  → [injected into] RecommendationService.run → mini_engine → (ROP,EOQ,SS,Max)
```

Non-intermittent regimes bypass statsforecast entirely (delegate to the deterministic projector).

---

## 5. Testing

- **`series.py`**: bucketed obs → ordered, zero-gap-filled series; days_per_period per bucket; empty history → `()`.
- **`classical.py`**: `forecast_rate` returns a sane positive rate on an intermittent fixture and `0.0` on all-zeros/short series (without invoking statsforecast); `select_model` returns `SBA` for a lumpy series (high CV²), `CROSTON` for a steady-intermittent series, `TSB` for a high-zero/obsolescent series. Pin one numeric expectation against a hand-checked statsforecast output (locks the wrapper to the reference).
- **`projector.py`**: an `INTERMITTENT` key yields a `COMPOUND_POISSON` `DemandProjection` whose `lambda` equals the fitted `fitted_per_day` (≠ the historical-average `lambda` for a front-loaded-recent series, proving the fit differs from the average); a `MODERATE`/`ULTRA_RARE` key returns exactly what the injected fallback returns (delegation).
- **`backtest.py`**: `mase` math on a known vector; `compare` over a small set returns a coherent `BacktestReport` (champion wins on a recency-trending fixture where the average lags).
- **#11 integration**: `RecommendationService(..., projector=StatisticalProjector())` runs over the committed extract sample and produces a `RecommendationBatch`; default (no projector arg) is unchanged.
- Conventions mirror the repo: `uv` + `pytest` + `ruff` (line-length 100, select E/F/I/B/UP/N/SIM), pydantic frozen where applicable, `pythonpath=["src"]`. Deps: `statsforecast>=2.0`, `scipy>=1.18`, `numba>=0.62` (pinned), `trax-io-reco` non-editable path source. Adversarial review of the fit→projection mapping + the SBC selection + the backtest after build.

---

## 6. Risks

- **statsforecast install fragility on 3.14.** Mitigation: pin `scipy>=1.18` + `numba>=0.62` (the verified-working floor); document why. A clean `uv sync` in the package must be a plan step.
- **Fitted intermittent rate parameterized as single-unit Poisson loses clump size.** Slice A keeps `clump_p=1.0` (matching #11's intermittent branch); compound-clump estimation is a later refinement. Documented.
- **Re-classification flapping / model instability** on tiny series. Mitigation: `forecast_rate` short-circuits all-zero/too-short series to `0.0`; `select_model` is deterministic; the regime gate (only `intermittent`) bounds inputs to where the methods are valid.
- **Provenance of the chosen model.** `DemandProjection` has no string field, so the model name is logged, not stored on the projection — the fitted `lambda` (≠ the deterministic average) already gives the policy provenance hash a distinct value. If first-class model provenance is later required, it is a one-field additive change to `DemandProjection` in #11.
