"""Render smoke tests (spec §3, §6): self-contained printable HTML, inline SVG only."""

from __future__ import annotations

import pytest

jinja2 = pytest.importorskip("jinja2", reason="bvr extra not installed")

from trax_io_spine.bvr.render import render_html  # noqa: E402
from trax_io_spine.bvr.svg import hbar  # noqa: E402


def test_hbar_is_wellformed_svg_with_labels():
    out = hbar([("holding", -14.58), ("ordering", 64.64), ("stockout", 1.33)])
    assert out.startswith("<svg") and out.endswith("</svg>")
    assert 'xmlns="http://www.w3.org/2000/svg"' in out
    assert "holding" in out and "ordering" in out
    assert out.count("<rect") >= 3


def test_render_html_contains_sections_and_hero_numbers(bvr_report):
    html = render_html(bvr_report)
    for heading in (
        "Executive summary", "Savings attribution (projected)", "Service posture",
        "Governance", "Forward look", "Methodology",
    ):
        assert heading in html
    assert "51.39" in html  # total projected
    assert "projected" in html.lower()
    assert "1/1 tiers at target posture" in html
    assert "<svg" in html


def test_render_html_is_self_contained(bvr_report):
    html = render_html(bvr_report)
    assert "http://" not in html.replace("http://www.w3.org/", "")  # only the SVG xmlns
    assert "https://" not in html
    assert "<script" not in html


def test_render_html_disclosures_present(bvr_report):
    html = render_html(bvr_report)
    assert "not realized" in html  # posture note
    assert "holding_cost_rate" in html  # assumption rates disclosed
    assert "1 of 1 changes valued" in html  # coverage disclosure
