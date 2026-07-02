"""Inline-SVG chart helpers — pure string builders (no JS, print-safe).

Deliberately replaces the April plan's Chart.js + headless-Chromium stack:
deterministic output, testable as strings, renders identically in WeasyPrint.
"""

from __future__ import annotations

from html import escape

_XMLNS = 'xmlns="http://www.w3.org/2000/svg"'
_POS = "#1a7f5a"  # projected benefit
_NEG = "#b3532e"  # projected cost
_BAR_H = 22
_GAP = 8
_LABEL_W = 170
_VALUE_W = 90


def hbar(items: list[tuple[str, float]], *, width: int = 640) -> str:
    """Horizontal bar chart: one row per (label, value); sign sets color + direction."""
    if not items:
        return f'<svg {_XMLNS} width="{width}" height="10"></svg>'
    scale_max = max(abs(v) for _, v in items) or 1.0
    track_w = width - _LABEL_W - _VALUE_W
    half = track_w / 2
    height = len(items) * (_BAR_H + _GAP)
    rows = []
    for i, (label, value) in enumerate(items):
        y = i * (_BAR_H + _GAP)
        w = abs(value) / scale_max * (half - 4)
        x = _LABEL_W + half - w if value < 0 else _LABEL_W + half
        color = _NEG if value < 0 else _POS
        rows.append(
            f'<text x="0" y="{y + 15}" font-size="12">{escape(label)}</text>'
            f'<rect x="{x:.1f}" y="{y + 2}" width="{max(w, 1):.1f}" '
            f'height="{_BAR_H - 6}" fill="{color}" />'
            f'<text x="{_LABEL_W + track_w + 6}" y="{y + 15}" font-size="12" '
            f'text-anchor="start">{value:,.2f}</text>'
        )
    axis = (
        f'<line x1="{_LABEL_W + half}" y1="0" x2="{_LABEL_W + half}" '
        f'y2="{height}" stroke="#999" stroke-width="1" />'
    )
    return (
        f'<svg {_XMLNS} width="{width}" height="{height}" role="img">'
        + axis + "".join(rows) + "</svg>"
    )


def tier_bars(tiers, *, width: int = 640) -> str:
    """Posture vs target per tier: filled bar = posture_rate, tick = target."""
    if not tiers:
        return f'<svg {_XMLNS} width="{width}" height="10"></svg>'
    track_w = width - _LABEL_W - _VALUE_W
    height = len(tiers) * (_BAR_H + _GAP)
    rows = []
    for i, t in enumerate(tiers):
        y = i * (_BAR_H + _GAP)
        fill_w = t.posture_rate * track_w
        tick_x = _LABEL_W + t.target_fill_rate * track_w
        rows.append(
            f'<text x="0" y="{y + 15}" font-size="12">Tier {t.tier} '
            f'({t.keys_at_posture}/{t.keys})</text>'
            f'<rect x="{_LABEL_W}" y="{y + 2}" width="{track_w}" '
            f'height="{_BAR_H - 6}" fill="#eee" />'
            f'<rect x="{_LABEL_W}" y="{y + 2}" width="{fill_w:.1f}" '
            f'height="{_BAR_H - 6}" fill="{_POS}" />'
            f'<line x1="{tick_x:.1f}" y1="{y}" x2="{tick_x:.1f}" y2="{y + _BAR_H - 2}" '
            f'stroke="#333" stroke-width="2" />'
            f'<text x="{_LABEL_W + track_w + 6}" y="{y + 15}" font-size="12">'
            f'{t.posture_rate:.0%}</text>'
        )
    return f'<svg {_XMLNS} width="{width}" height="{height}" role="img">' + "".join(rows) + "</svg>"
