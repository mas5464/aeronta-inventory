"""Enumerations for the recommendation engine.

The spine/forecasting mirrors (Regime, CanonicalCriticality, PolicyKind, AutonomyTier,
ForecastHorizon) are kept field-for-field — including member NAMES — so they promote
unchanged to trax_io.contracts.* when the Agent Spine (#4) lands (spec §5.1).
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class Regime(StrEnum):
    """Demand regime for a (PN, Location). Mirrors trax_io.contracts.regime.Regime."""

    ULTRA_RARE = "ultra_rare"
    INTERMITTENT = "intermittent"
    MODERATE = "moderate"
    HIGH_VOLUME = "high_volume"


class CanonicalCriticality(IntEnum):
    """Normalized 5-tier essentiality. Ordered so `criticality <= TIER_2` works."""

    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
    TIER_4 = 4
    TIER_5 = 5


class PolicyKind(StrEnum):
    """Inventory policy family produced by the policy engine."""

    BASE_STOCK = "base_stock"
    S_S = "s_S"
    R_Q = "R_Q"


class AutonomyTier(IntEnum):
    """Three-tier autonomy posture (Tier A/B/C). Mirror only — the engine suggests,
    the Guardrail specialist (#4) enforces (spec §2.3)."""

    ADVISOR = 1
    BOUNDED = 2
    AUTONOMOUS = 3


class ForecastHorizon(IntEnum):
    """Forecast horizon mirror. Named members preserved for promotion fidelity.

    The engine's own ``horizon_days`` fields are free positive ints (protection
    periods vary per part) and are intentionally decoupled from this enum (spec §5.1).
    """

    DAYS_30 = 30
    DAYS_60 = 60
    DAYS_90 = 90
    DAYS_180 = 180


class EvidenceKind(StrEnum):
    """Kinds of supporting evidence attached to a recommendation."""

    WORK_ORDER = "work_order"
    MAINTENANCE_EVENT = "maintenance_event"
    TASK_CARD = "task_card"
    REQUISITION = "requisition"
    OPEN_ORDER = "open_order"
    DEMAND_HISTORY = "demand_history"
    DONOR_STOCK = "donor_stock"
    SHELF_LIFE = "shelf_life"
    AOG_EVENT = "aog_event"


class AogRiskLevel(IntEnum):
    """AOG (Aircraft On Ground) risk annotation. Ordered for thresholding."""

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class RecommendationType(StrEnum):
    """The five deterministic recommendation types (spec §3.2)."""

    PURCHASE = "purchase"
    TRANSFER = "transfer"
    REDUCE_STOCK = "reduce_stock"
    SELL = "sell"
    ADJUST_MIN_MAX = "adjust_min_max"
