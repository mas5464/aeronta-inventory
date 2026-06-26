"""Trax IO deterministic inventory recommendation engine.

Turns eMRO feature data into ranked Purchase / Transfer / Reduce Stock / Sell /
Adjust Min-Max recommendations over a shared net-position primitive. No LLM in the
path; forward-compatible with the Agent Spine (#4) and Forecasting (#5) contracts.

See docs/superpowers/specs/2026-04-17-trax-io-recommendation-engine-design.md.
"""

__version__ = "0.1.0"
