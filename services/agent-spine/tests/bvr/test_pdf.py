"""Skip-gated PDF round-trip (spec §3): weasyprint is an optional extra whose native
libs (pango/cairo) may be absent — tests must skip cleanly, never fail, without them.
macOS local runs need: DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
"""

from __future__ import annotations

import pytest


def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:  # ImportError OR OSError from missing native libs
        return False


pytestmark = pytest.mark.skipif(
    not _weasyprint_available(),
    reason="weasyprint (pdf extra) not installed or native pango/cairo libs not loadable",
)


def test_render_pdf_round_trip(bvr_report):
    from trax_io_spine.bvr.pdf import render_pdf
    from trax_io_spine.bvr.render import render_html

    pdf = render_pdf(render_html(bvr_report))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000
