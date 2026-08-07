"""Pure, budget-independent candidate-frontier planning primitives."""

from trax_io_reco.candidate.frontier import PrunedCandidates, dominates, prune_dominated
from trax_io_reco.candidate.identity import (
    candidate_identifier,
    canonical_json,
    content_digest,
    frontier_fingerprint,
    output_digest,
)
from trax_io_reco.candidate.integration import (
    candidate_from_finalized_recommendations,
    no_change_from_calculation_evidence,
    no_change_from_finalized_recommendation,
)
from trax_io_reco.candidate.models import model_identity_from_served
from trax_io_reco.candidate.planner import CandidatePlanner
from trax_io_reco.candidate.reconcile import (
    build_no_change_candidate,
    build_transfer_purchase_candidate,
    reconcile_candidate,
)

__all__ = [
    "CandidatePlanner",
    "PrunedCandidates",
    "build_no_change_candidate",
    "build_transfer_purchase_candidate",
    "candidate_identifier",
    "candidate_from_finalized_recommendations",
    "canonical_json",
    "content_digest",
    "dominates",
    "frontier_fingerprint",
    "model_identity_from_served",
    "no_change_from_calculation_evidence",
    "no_change_from_finalized_recommendation",
    "output_digest",
    "prune_dominated",
    "reconcile_candidate",
]
