import pytest

from trax_io_forecasting.classical import ClassicalModel, forecast_rate, select_model


def test_forecast_rate_pins_croston_reference() -> None:
    # Hand-checked against statsforecast CrostonClassic on this intermittent series (~0.35/period).
    y = [0, 0, 1, 0, 0, 0, 2, 0, 0, 1, 0, 0]
    assert forecast_rate(y, ClassicalModel.CROSTON) == pytest.approx(0.35, abs=0.05)


def test_forecast_rate_zero_on_degenerate() -> None:
    assert forecast_rate([0, 0, 0, 0], ClassicalModel.SBA) == 0.0
    assert forecast_rate([5], ClassicalModel.CROSTON) == 0.0  # len < 2


def test_select_model_lumpy_is_sba() -> None:
    # high CV^2 (sizes 1 and 9), moderate intermittence
    assert select_model([1, 0, 9, 0, 1, 0, 9, 0]) == ClassicalModel.SBA


def test_select_model_steady_intermittent_is_croston() -> None:
    # even sizes, ADI < 2 -> Croston
    assert select_model([1, 0, 1, 1, 0, 1, 1, 0]) == ClassicalModel.CROSTON


def test_select_model_very_sparse_is_tsb() -> None:
    # ADI >= 2.0 (1 demand every ~4 periods) -> TSB
    assert select_model([0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]) == ClassicalModel.TSB
