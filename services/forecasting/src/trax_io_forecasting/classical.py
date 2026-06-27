"""statsforecast classical intermittent-demand models + Syntetos-Boylan-Croston selection."""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

_LUMPY_CV2 = 0.49
_OBSOLESCENCE_ADI = 2.0


class ClassicalModel(StrEnum):
    CROSTON = "croston"
    SBA = "sba"
    TSB = "tsb"


def forecast_rate(values: Sequence[float], model: ClassicalModel) -> float:
    vals = [float(v) for v in values]
    if len(vals) < 2 or sum(vals) <= 0.0:
        return 0.0

    import numpy as np
    from statsforecast.models import TSB, CrostonClassic, CrostonSBA

    estimator = {
        ClassicalModel.CROSTON: CrostonClassic(),
        ClassicalModel.SBA: CrostonSBA(),
        ClassicalModel.TSB: TSB(alpha_d=0.1, alpha_p=0.1),
    }[model]
    rate = float(estimator.forecast(y=np.asarray(vals, dtype=np.float64), h=1)["mean"][0])
    return rate if math.isfinite(rate) and rate > 0.0 else 0.0


def select_model(values: Sequence[float]) -> ClassicalModel:
    vals = [float(v) for v in values]
    nonzero = [v for v in vals if v > 0.0]
    if len(nonzero) < 2:
        return ClassicalModel.CROSTON
    adi = len(vals) / len(nonzero)
    mean_nz = sum(nonzero) / len(nonzero)
    cv2 = (sum((v - mean_nz) ** 2 for v in nonzero) / len(nonzero)) / (mean_nz**2)
    if adi > _OBSOLESCENCE_ADI:
        return ClassicalModel.TSB
    if cv2 >= _LUMPY_CV2:
        return ClassicalModel.SBA
    return ClassicalModel.CROSTON
