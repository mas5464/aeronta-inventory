"""Stable facade for deterministic per-key candidate frontiers."""

from __future__ import annotations

from dataclasses import dataclass

from trax_io_reco.candidate.frontier import prune_dominated
from trax_io_reco.candidate.identity import (
    candidate_identifier,
    frontier_fingerprint,
    output_digest,
)
from trax_io_reco.contracts.candidate import (
    CANDIDATE_PLANNER_VERSION,
    CandidateFingerprintInputs,
    CandidateFrontier,
    PolicyCandidate,
)


@dataclass(frozen=True)
class CandidatePlanner:
    version: str = CANDIDATE_PLANNER_VERSION

    def __post_init__(self) -> None:
        if self.version != CANDIDATE_PLANNER_VERSION:
            raise ValueError(f"unsupported CandidatePlanner version: {self.version}")

    def fingerprint(self, inputs: CandidateFingerprintInputs) -> str:
        if inputs.candidate_planner_version != self.version:
            raise ValueError("fingerprint planner version does not match CandidatePlanner")
        return frontier_fingerprint(inputs)

    def build_frontier(
        self,
        *,
        inputs: CandidateFingerprintInputs,
        candidates: tuple[PolicyCandidate, ...],
    ) -> CandidateFrontier:
        fingerprint = self.fingerprint(inputs)
        if not candidates:
            raise ValueError("candidate menu must not be empty")
        if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
            raise ValueError("candidate menu contains duplicate candidate ids")
        if sum(candidate.is_no_change for candidate in candidates) != 1:
            raise ValueError("candidate menu must contain exactly one no-change candidate")
        for candidate in candidates:
            if candidate.tenant_id != inputs.tenant_id:
                raise ValueError("candidate tenant does not match fingerprint inputs")
            if candidate.decision_key != inputs.decision_key:
                raise ValueError("candidate decision key does not match fingerprint inputs")
            if candidate.member_keys != inputs.member_keys:
                raise ValueError("candidate member keys do not match fingerprint inputs")
            if candidate.lifecycle_costs.currency != inputs.currency:
                raise ValueError("candidate currency does not match fingerprint inputs")
            if candidate.model_identity != inputs.model_identity:
                raise ValueError("candidate model identity does not match fingerprint inputs")
            expected_id = candidate_identifier(fingerprint, candidate)
            if candidate.candidate_id != expected_id:
                raise ValueError(
                    f"candidate {candidate.candidate_id} was not built for this fingerprint"
                )

        pruned = prune_dominated(candidates)
        digest = output_digest(
            frontier_id=fingerprint,
            tenant_id=inputs.tenant_id,
            decision_key=inputs.decision_key,
            member_keys=inputs.member_keys,
            currency=inputs.currency,
            candidates=pruned.candidates,
            dominated_options_removed=pruned.removed_count,
        )
        return CandidateFrontier(
            frontier_fingerprint=fingerprint,
            output_digest=digest,
            planner_version=self.version,
            tenant_id=inputs.tenant_id,
            decision_key=inputs.decision_key,
            member_keys=inputs.member_keys,
            currency=inputs.currency,
            candidates=pruned.candidates,
            total_options_considered=len(candidates),
            dominated_options_removed=pruned.removed_count,
        )


__all__ = ["CandidatePlanner"]
