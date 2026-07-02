"""WeasyPrint PDF rendering — optional `pdf` extra (spec §3).

Lazy import: the module is importable without weasyprint; `render_pdf` raises
`PdfUnavailable` with actionable detail when the extra or its native libs
(pango/cairo) are absent. macOS local: DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib.
The Docker BFF image installs the libs via apt (deploy/bff.Dockerfile).
"""

from __future__ import annotations


class PdfUnavailable(RuntimeError):  # noqa: N818
    """The pdf extra (weasyprint) or its native libraries are not installed."""


def render_pdf(html: str) -> bytes:
    try:
        import weasyprint
    except Exception as exc:  # ImportError or OSError (missing pango/cairo dylibs)
        raise PdfUnavailable(
            "PDF rendering requires the 'pdf' extra (weasyprint) and its native "
            "pango/cairo libraries — install with `uv sync --extra pdf`; on macOS "
            "also set DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib"
        ) from exc
    return weasyprint.HTML(string=html).write_pdf()
