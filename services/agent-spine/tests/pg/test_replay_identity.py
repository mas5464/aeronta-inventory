from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from trax_io_reco.contracts.replay import ReplayEvaluationRequest

from tests.replay_builders import matched_replay_request, replay_request
from trax_io_spine.pg.replay import (
    ReplayRunHeader,
    _replay_run_identity,
    replay_fingerprint,
)


def test_replay_fingerprint_is_stable_and_sensitive_to_complete_universe() -> None:
    original = replay_request("tenant-a")
    equivalent = original.model_copy(deep=True)
    changed = replay_request(
        "tenant-a",
        universe_id="historical-decisions-2026q2",
    )

    assert replay_fingerprint(original) == replay_fingerprint(equivalent)
    assert replay_fingerprint(original) != replay_fingerprint(changed)
    assert replay_fingerprint(original).startswith("replay_")
    assert len(replay_fingerprint(original)) == len("replay_") + 64


def test_run_header_identity_binds_trusted_digest_and_normalized_config() -> None:
    header = ReplayRunHeader(
        tenant_id="tenant-a",
        currency="USD",
        universe_ref="approved-alias",
        universe_id="historical-decisions",
        universe_sha256="a" * 64,
        trusted_input_sha256="b" * 64,
        expected_decision_count=1,
        observation_count=0,
        exclusion_count=1,
        current_policy_label="current",
        challenger_policy_label="repair-aware",
        comparison_rule="matched_budget",
        match_tolerance=Decimal("0"),
    )
    identity = _replay_run_identity(header)

    assert _replay_run_identity(
        header.model_copy(update={"universe_ref": "same-evidence-alias"})
    ) == identity
    assert _replay_run_identity(
        header.model_copy(update={"trusted_input_sha256": "c" * 64})
    ) != identity
    assert _replay_run_identity(
        header.model_copy(update={"challenger_policy_label": "tampered"})
    ) != identity
    assert _replay_run_identity(
        header.model_copy(update={"match_tolerance": Decimal("0.01")})
    ) != identity


def test_planning_selection_link_is_canonical_and_no_lookahead_bound() -> None:
    request = matched_replay_request(
        "tenant-a",
        include_planning_links=True,
    )
    original_fingerprint = replay_fingerprint(request)
    changed_payload = request.model_dump()
    changed_payload["observations"][0]["current_lineage"][
        "planning_run_id"
    ] = "33333333-3333-3333-3333-333333333333"
    changed = ReplayEvaluationRequest.model_validate(changed_payload)

    assert replay_fingerprint(changed) != original_fingerprint

    wrong_selection = request.model_dump()
    wrong_selection["observations"][0]["current_lineage"][
        "planning_selection_decision_key"
    ] = "PN-OTHER@MIA"
    with pytest.raises(
        ValidationError,
        match="planning selection link does not match replay decision",
    ):
        ReplayEvaluationRequest.model_validate(wrong_selection)

    late_selection = request.model_dump()
    late_selection["observations"][0]["current_lineage"][
        "planning_selection_available_at"
    ] = request.observations[0].as_of + timedelta(microseconds=1)
    with pytest.raises(
        ValidationError,
        match="no-lookahead violation for planning selection link",
    ):
        ReplayEvaluationRequest.model_validate(late_selection)
