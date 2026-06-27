def test_deps_importable() -> None:
    from statsforecast.models import CrostonClassic  # noqa: F401
    from trax_io_reco.demand.projection import HistoricalScheduledProjector  # noqa: F401

    import trax_io_forecasting  # noqa: F401

    assert trax_io_forecasting.__version__ == "0.1.0"
