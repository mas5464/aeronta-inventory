"""Trax IO Forecasting (#5). Slice A: classical intermittent demand forecasting.

A `StatisticalProjector` implements #11's `DemandProjector` Protocol with statsforecast
Croston/SBA/TSB for the `intermittent` regime, delegating other regimes to the deterministic
projector. See docs/superpowers/specs/2026-06-27-forecasting-classical-intermittent-design.md.
"""

__version__ = "0.1.0"
