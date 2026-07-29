"""Canonical identity and bounded metadata for portfolio-planning inputs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from trax_io_spine.bff.models import PartContext

PLANNING_INPUTS_CONTRACT_VERSION = "planning-inputs.v1"


@dataclass(frozen=True)
class PlanningInputSnapshot:
    """One transactionally consistent planning-input read.

    ``contexts`` contains the complete requested scope, but the header remains
    bounded regardless of portfolio size. For the all-eligible read, contexts
    use canonical decision-key order; explicit reads preserve caller order.
    """

    contexts: tuple[PartContext, ...]
    source_snapshot_hash: str
    source_generation_hash: str
    coverage: dict[str, int]
    seeded_at: datetime | None


def _document(context: PartContext | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(context, Mapping):
        return context
    return context.model_dump(mode="json")


def _frontier(document: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = document.get("candidate_frontier")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("planning input candidate_frontier must be an object")
    return value


def _criticality(document: Mapping[str, Any]) -> int | None:
    attributes = document.get("attributes")
    if not isinstance(attributes, Mapping):
        raise ValueError("planning input attributes must be an object")
    value = attributes.get("criticality_tier")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("planning input criticality_tier must be an integer or null")
    return value


def planning_input_source_snapshot_hash(
    contexts: Iterable[PartContext | Mapping[str, Any]],
    *,
    coverage: Mapping[str, int] | None = None,
) -> str:
    """Hash candidate identity, criticality, and optional universe coverage.

    All-eligible callers should provide the bounded authoritative coverage so a
    change to the excluded/missing-frontier population cannot reuse an older
    run whose eligible candidate artifacts happen to be unchanged.
    """

    identities: list[dict[str, Any]] = []
    decision_keys: set[str] = set()
    for context in contexts:
        document = _document(context)
        frontier = _frontier(document)
        if frontier is None:
            pn = document.get("pn")
            location = document.get("location")
            if not isinstance(pn, str) or not pn or not isinstance(location, str) or not location:
                raise ValueError("planning input must identify a non-empty part and location")
            decision_key = f"{pn}@{location}"
            identity: dict[str, Any] = {
                "decision_key": decision_key,
                "criticality_tier": _criticality(document),
                "candidate_frontier": None,
            }
        else:
            decision_key = frontier.get("decision_key")
            if not isinstance(decision_key, str) or not decision_key:
                raise ValueError("planning input frontier must identify a decision key")
            identity = {
                "decision_key": decision_key,
                "criticality_tier": _criticality(document),
                "candidate_frontier": {
                    "tenant_id": frontier.get("tenant_id"),
                    "member_keys": frontier.get("member_keys"),
                    "currency": frontier.get("currency"),
                    "frontier_fingerprint": frontier.get("frontier_fingerprint"),
                    "output_digest": frontier.get("output_digest"),
                    "planner_version": frontier.get("planner_version"),
                },
            }
        if decision_key in decision_keys:
            raise ValueError(f"duplicate planning input decision key: {decision_key}")
        decision_keys.add(decision_key)
        identities.append(identity)

    payload: object = sorted(identities, key=lambda item: item["decision_key"])
    if coverage is not None:
        normalized_coverage: dict[str, int] = {}
        for field in (
            "total_key_count",
            "returned_key_count",
            "eligible_key_count",
            "missing_frontier_key_count",
            "candidate_count",
            "feasible_candidate_count",
            "criticality_known_key_count",
            "criticality_unknown_key_count",
        ):
            value = coverage.get(field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(
                    f"planning input coverage {field} must be a non-negative integer"
                )
            normalized_coverage[field] = value
        payload = {
            "inputs": payload,
            "coverage": normalized_coverage,
        }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return f"candidate_snapshot_{hashlib.sha256(encoded.encode()).hexdigest()}"


def planning_input_source_generation_hash(source_snapshot_hash: str) -> str:
    """Derive one common full-universe marker shared by every scoped read."""

    if (
        not isinstance(source_snapshot_hash, str)
        or not source_snapshot_hash
        or len(source_snapshot_hash) > 512
    ):
        raise ValueError("planning source snapshot hash is invalid")
    encoded = (
        "planning-input-generation.v1:" + source_snapshot_hash
    ).encode()
    return f"planning_generation_{hashlib.sha256(encoded).hexdigest()}"


def planning_input_coverage(
    contexts: Iterable[PartContext | Mapping[str, Any]],
    *,
    total_key_count: int | None = None,
    returned_key_count: int | None = None,
) -> dict[str, int]:
    """Return fixed-cardinality coverage counters for a planning-input scope."""

    documents = tuple(_document(context) for context in contexts)
    total = len(documents) if total_key_count is None else total_key_count
    returned = len(documents) if returned_key_count is None else returned_key_count
    if total < 0 or returned < 0 or returned > total:
        raise ValueError("planning input coverage counts do not reconcile")

    eligible = 0
    candidate_count = 0
    feasible_candidate_count = 0
    criticality_known = 0
    for document in documents:
        if _criticality(document) is not None:
            criticality_known += 1
        frontier = _frontier(document)
        if frontier is None:
            continue
        candidates = frontier.get("candidates")
        if not isinstance(candidates, (list, tuple)) or not candidates:
            raise ValueError("eligible planning input frontier must contain candidates")
        eligible += 1
        candidate_count += len(candidates)
        feasible_candidate_count += sum(
            1
            for candidate in candidates
            if isinstance(candidate, Mapping) and candidate.get("feasible") is True
        )

    return {
        "total_key_count": total,
        "returned_key_count": returned,
        "eligible_key_count": eligible,
        "missing_frontier_key_count": total - eligible,
        "candidate_count": candidate_count,
        "feasible_candidate_count": feasible_candidate_count,
        "criticality_known_key_count": criticality_known,
        "criticality_unknown_key_count": len(documents) - criticality_known,
    }


def planning_input_model_profile(
    contexts: Iterable[PartContext | Mapping[str, Any]],
) -> dict[str, str]:
    """Summarize trusted candidate/model versions in fixed-cardinality fields."""

    versions = {
        "tenant_policy_version": set(),
        "forecast_version": set(),
        "repair_model_version": set(),
        "candidate_planner_version": set(),
    }
    for context in contexts:
        frontier = _frontier(_document(context))
        if frontier is None:
            continue
        planner_version = frontier.get("planner_version")
        if isinstance(planner_version, str) and planner_version:
            versions["candidate_planner_version"].add(planner_version)
        candidates = frontier.get("candidates")
        if not isinstance(candidates, (list, tuple)):
            raise ValueError("planning input frontier candidates must be an array")
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                raise ValueError("planning input candidate must be an object")
            identity = candidate.get("model_identity")
            if not isinstance(identity, Mapping):
                raise ValueError("planning input candidate model identity is missing")
            for output_field, source_field in (
                ("tenant_policy_version", "policy_version"),
                ("forecast_version", "forecast_version"),
                ("repair_model_version", "repair_version"),
            ):
                value = identity.get(source_field)
                if isinstance(value, str) and value:
                    versions[output_field].add(value)

    def _one(field: str) -> str:
        values = versions[field]
        if not values:
            return f"{field.removesuffix('_version')}-unavailable"
        return next(iter(values)) if len(values) == 1 else "+".join(sorted(values))

    return {field: _one(field) for field in versions}


__all__ = [
    "PLANNING_INPUTS_CONTRACT_VERSION",
    "PlanningInputSnapshot",
    "planning_input_coverage",
    "planning_input_model_profile",
    "planning_input_source_generation_hash",
    "planning_input_source_snapshot_hash",
]
