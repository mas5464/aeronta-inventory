# Trax IO Forecasting — service

Sub-project #5, slice A. `StatisticalProjector` (a `DemandProjector`) applies statsforecast
classical models (Croston / SBA / TSB, SBC-selected) to the `intermittent` regime, producing a
fitted `COMPOUND_POISSON` `DemandProjection`; other regimes delegate to the deterministic
`HistoricalScheduledProjector`. A MASE backtest scores fitted-vs-average.

## Dev setup
```bash
cd services/forecasting
uv sync --extra dev
uv run --extra dev pytest
uv run --extra dev ruff check .
```

`statsforecast` runs on Python 3.14 only with `scipy>=1.18` + `numba>=0.62` (pinned in
`pyproject.toml`). Inject the forecaster into #11 with
`RecommendationService(..., projector=StatisticalProjector())`.
