from trax_io_spine.guardrail.messages import humanize_guardrail_codes


def test_drops_non_policy_recommendation() -> None:
    assert humanize_guardrail_codes(("non_policy_recommendation",)) == ()


def test_delta_exceeds_100pct_message() -> None:
    assert humanize_guardrail_codes(("delta_exceeds_100pct",)) == (
        "Exceeds the 100% single-write cap — requires manual review.",
    )


def test_delta_gt_100pct_message_is_identical() -> None:
    assert humanize_guardrail_codes(("delta_gt_100pct",)) == (
        "Exceeds the 100% single-write cap — requires manual review.",
    )


def test_both_delta_codes_collapse_to_one_message() -> None:
    assert humanize_guardrail_codes(("delta_exceeds_100pct", "delta_gt_100pct")) == (
        "Exceeds the 100% single-write cap — requires manual review.",
    )


def test_active_aog_message() -> None:
    assert humanize_guardrail_codes(("active_aog",)) == (
        "An aircraft is currently AOG for this part — routed for immediate review.",
    )


def test_shelf_life_clamped_message() -> None:
    assert humanize_guardrail_codes(("shelf_life_clamped",)) == (
        "Quantity capped to respect this part's shelf life.",
    )


def test_hazmat_tool_capped_message() -> None:
    assert humanize_guardrail_codes(("hazmat_tool_capped",)) == (
        "Increase capped — hazmat/tool-control items can only double per cycle.",
    )


def test_open_order_deferral_message() -> None:
    assert humanize_guardrail_codes(("open_order_deferral",)) == (
        "Deferred — on-hand stock plus incoming orders already cover the proposed level.",
    )


def test_unknown_code_falls_back_to_title_case() -> None:
    assert humanize_guardrail_codes(("some_future_code",)) == ("Some Future Code",)


def test_empty_input_returns_empty() -> None:
    assert humanize_guardrail_codes(()) == ()


def test_dedupes_repeated_codes() -> None:
    assert humanize_guardrail_codes(("active_aog", "active_aog")) == (
        "An aircraft is currently AOG for this part — routed for immediate review.",
    )


def test_preserves_first_seen_order_across_distinct_messages() -> None:
    result = humanize_guardrail_codes(("shelf_life_clamped", "active_aog"))
    assert result == (
        "Quantity capped to respect this part's shelf life.",
        "An aircraft is currently AOG for this part — routed for immediate review.",
    )


def test_full_realistic_rejection_tuple() -> None:
    # A real REJECTED_HARD_GUARDRAIL outcome: the spine's own delta violation +
    # the engine's own pre-check flag + an unrelated engine flag, all in one
    # tuple (mirrors guardrail/enforce.py:42's `violations + rec.guardrail_flags`).
    result = humanize_guardrail_codes(("delta_exceeds_100pct", "delta_gt_100pct", "active_aog"))
    assert result == (
        "Exceeds the 100% single-write cap — requires manual review.",
        "An aircraft is currently AOG for this part — routed for immediate review.",
    )
