# ADR-0006: #5 forecasting swaps in behind #11's DemandProjector seam (statsforecast classical, slice A)

**Date:** 2026-06-27
**Status:** Accepted
**Context project:** #5 Forecasting & Policy Engine (slice A)

## Context

The 2026-04-14 #5 sub-plan framed Forecasting & Policy Engine as two Strands specialists with SageMaker-hosted models. By the time #5 was built, two facts reshaped it:

1. **The "Policy Engine" half already exists.** #11's deterministic `mini_engine` turns a `DemandProjection` + lead-time + service-level + §6.2 constraints into `(ROP, EOQ, SS, Max)`. #5's net-new is the **forecasting** that feeds it.
2. **The integration seam already exists.** #11 defines `DemandProjectorProtocol.project(*, context, regime) -> DemandProjection`, and the policy engine consumes the `DemandProjection`. Today `RecommendationService` hardcodes the deterministic `HistoricalScheduledProjector`.

#5 is also far too large for one cut (a 45-task sub-plan with SageMaker, LightGBM, Chronos, empirical-Bayes, champion/challenger auto-promotion). It needs decomposition.

## Decision

Build #5 as a sequence of slices, each a `DemandProjector` implementation injected into #11 — **not** as a separate SageMaker-hosted agent for v1.

**Slice A (this ADR):** a `StatisticalProjector` (`services/forecasting/`, `trax_io_forecasting`) that applies statsforecast classical models (Croston / SBA / TSB, auto-selected by the Syntetos-Boylan-Croston ADI/CV² classification) to the **`intermittent`** regime, and **delegates every other regime to the deterministic projector**. For intermittent it reuses #11's **exact** distribution machinery — `COMPOUND_POISSON`, `λ = fitted_per_day`, `std = sqrt(λ)`, identical `scheduled_component`/`by_aircraft`/`by_task` — changing only the λ *source* from the historical average to the fitted rate. A MASE backtest scores fitted-vs-average. One backward-compatible #11 change makes `RecommendationService`'s projector injectable (`projector=None` → `HistoricalScheduledProjector`).

Scope was tightened to **intermittent only**: `ultra_rare` (<6 events/24mo) lacks single-series signal for Croston (the design uses empirical-Bayes peer priors — slice C); `moderate`/`high_volume` want LightGBM (slice B).

This mirrors the project's repeated pattern: Protocol-first, deterministic-default, the heavier impl injected behind the same seam (cf. ADR-0002 feature store, ADR-0005 agent spine, the Cedar/event-lane slices).

**Grounded dependency decision:** the env is Python 3.14; statsforecast 2.0.x installs and runs here **only with `scipy>=1.18` + `numba>=0.62` pinned** — without the pins the resolver selects an old scipy with no 3.14 wheel and fails a meson source build. These pins are mandatory in `pyproject.toml`. statsforecast is lazy-imported inside `forecast_rate` so the rest of the package imports without warming the JIT.

## Consequences

**Positive**
- The fitted projection is **structurally indistinguishable** from the deterministic one to the policy engine — only a better λ. So slice A improves recommendation quality with zero policy-engine change, and the engine's existing provenance hash already distinguishes a fitted projection.
- No AWS/SageMaker for v1: fit/forecast/backtest run in-process; fully locally verifiable (19 forecasting tests; #11's 142 unchanged).
- Later slices (LightGBM, Chronos, empirical-Bayes, champion/challenger auto-promotion, SageMaker hosting) are pure additions behind the same `DemandProjector` seam.

**Negative / deferred**
- Intermittent-only; `ultra_rare`/`moderate`/`high_volume` keep the deterministic projection until slices B/C.
- The fitted intermittent rate is parameterized as single-unit Poisson (`clump_p=1.0`, matching #11); compound-clump estimation is deferred.
- statsforecast adds a heavy dep tree (numba/llvmlite/pandas) and a version-pin requirement on Python 3.14.

## Alternatives considered

1. **Pure-numpy Croston/TSB/SBA (no statsforecast).** Leaner + version-stable, but re-implements reference methods; rejected in favor of statsforecast (the design's stated tool, reference-correct, easy to extend) with the documented pins.
2. **A separate SageMaker-hosted ForecastingAgent now (faithful to the 2014-plan framing).** Rejected for v1: AWS-coupled, not locally verifiable, and unnecessary for the classical models which run in-process.
3. **Forecasting produces the slim `ForecastDistribution` mirror.** Rejected: the policy engine consumes `DemandProjection` directly via the existing seam; the slim mirror is for the deferred LLM-agent contract.
