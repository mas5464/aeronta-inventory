"""Jinja2 HTML renderer for the BVR (spec §3) — one self-contained printable page."""

from __future__ import annotations

from jinja2 import Environment, PackageLoader

from trax_io_spine.bvr import svg
from trax_io_spine.bvr.models import BvrReport

_env = Environment(
    loader=PackageLoader("trax_io_spine.bvr", "templates"),
    # Unconditional: select_autoescape(["html"]) checks the literal final
    # suffix, which for "bvr.html.j2" is ".j2" — not in the allow-list — so
    # it silently disabled escaping for the whole template. We render exactly
    # one HTML template; the two chart injections already use `| safe`.
    autoescape=True,
)


def render_html(report: BvrReport) -> str:
    savings_chart = svg.hbar([
        ("Holding cost", float(report.savings.holding_cost_delta.amount)),
        ("Ordering cost", float(report.savings.ordering_cost_delta.amount)),
        ("Stockout risk", float(report.savings.stockout_risk_delta.amount)),
    ])
    posture_chart = svg.tier_bars(report.service_posture.tiers)
    return _env.get_template("bvr.html.j2").render(
        r=report, savings_chart=savings_chart, posture_chart=posture_chart
    )
