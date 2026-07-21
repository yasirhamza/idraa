"""Reportlab styling vocabulary for the PDF run report (design-system aligned).

Single source for the PDF's colors, type scale, table style, and chart theme.
Colors are the design tokens' LIGHT values (PDF is print → light-only); the
chart pair is imported from services.chart_palette so PDF and web chart colors
are the same object. tests/unit/test_pdf_theme.py pins all of it.
"""

from __future__ import annotations

from typing import Any

from reportlab.graphics.shapes import Circle, Drawing, Path
from reportlab.lib import colors
from reportlab.lib.colors import Color
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import TableStyle

from idraa.services.chart_palette import CHART_SERIES

_H = colors.HexColor


class PDFColors:
    """Design tokens (app.css :root, light) as reportlab HexColors."""

    brand = _H("#0F4C81")
    ink1 = _H("#18181B")
    ink2 = _H("#52525B")
    ink3 = _H("#A1A1AA")
    surface0 = _H("#FAFAF9")
    surface1 = _H("#FFFFFF")
    surface2 = _H("#F4F4F5")
    border_subtle = _H("#E4E4E7")
    border_strong = _H("#D4D4D8")
    status_critical = _H("#B91C1C")
    status_warning = _H("#B45309")
    status_success = _H("#15803D")
    status_info = _H("#1D4ED8")
    numeric_pos = _H("#15803D")
    numeric_neg = _H("#B91C1C")
    # Chart pair — imported, not re-typed, so it's the SAME value as the web.
    chart_inherent = _H(CHART_SERIES["inherent"]["light"])
    chart_residual = _H(CHART_SERIES["residual"]["light"])


CHART: dict[str, colors.Color] = {
    "inherent": PDFColors.chart_inherent,  # without controls
    "residual": PDFColors.chart_residual,  # with controls
    "reduction": PDFColors.numeric_pos,  # reduction bar
    "tolerance": PDFColors.status_critical,
    "axis": PDFColors.ink2,
    "grid": PDFColors.border_subtle,
    "title": PDFColors.ink1,
}

# Type scale: (fontName, fontSize_pt, leading_pt, textColor, extra kwargs).
# Sizes are the design rem scale converted to print points.
_SCALE: dict[str, dict[str, Any]] = {
    "display": {
        "fontName": "Helvetica-Bold",
        "fontSize": 28,
        "leading": 32,
        "textColor": PDFColors.ink1,
    },
    "h1": {
        "fontName": "Helvetica-Bold",
        "fontSize": 18,
        "leading": 22,
        "textColor": PDFColors.ink1,
    },
    "h2": {
        "fontName": "Helvetica-Bold",
        "fontSize": 14,
        "leading": 18,
        "textColor": PDFColors.ink1,
    },
    "h3": {
        "fontName": "Helvetica-Bold",
        "fontSize": 11,
        "leading": 15,
        "textColor": PDFColors.ink1,
    },
    "body": {"fontName": "Helvetica", "fontSize": 10, "leading": 14, "textColor": PDFColors.ink2},
    "meta": {
        "fontName": "Helvetica-Bold",
        "fontSize": 8,
        "leading": 11,
        "textColor": PDFColors.ink3,
    },
    "micro": {"fontName": "Helvetica", "fontSize": 8, "leading": 11, "textColor": PDFColors.ink3},
    "number_lg": {
        "fontName": "Helvetica-Bold",
        "fontSize": 22,
        "leading": 26,
        "textColor": PDFColors.ink1,
    },
    "number_md": {
        "fontName": "Helvetica-Bold",
        "fontSize": 11,
        "leading": 14,
        "textColor": PDFColors.ink1,
    },
    "caption": {"fontName": "Helvetica", "fontSize": 9, "leading": 12, "textColor": PDFColors.ink3},
    "footer": {"fontName": "Helvetica", "fontSize": 8, "leading": 10, "textColor": PDFColors.ink3},
    "wordmark": {
        "fontName": "Helvetica-Bold",
        "fontSize": 20,
        "leading": 24,
        "textColor": PDFColors.brand,
    },
    # Important-1 (final-review): the ONE header treatment for every table in
    # the report — upright Helvetica-Bold, ink2 (~7:1 on surface2 = AA), 8pt.
    # Used for Paragraph-cell header rows; plain-string header rows get the
    # equivalent TEXTCOLOR/FONTNAME via table_style()'s TableStyle commands
    # (those commands are inert on Paragraph cells — a Paragraph carries its
    # own ParagraphStyle, so Paragraph headers must use this style directly).
    "table_header": {
        "fontName": "Helvetica-Bold",
        "fontSize": 8,
        "leading": 11,
        "textColor": PDFColors.ink2,
    },
    "table_header_right": {
        "fontName": "Helvetica-Bold",
        "fontSize": 8,
        "leading": 11,
        "textColor": PDFColors.ink2,
        "alignment": TA_RIGHT,
    },
}


def para(style_name: str, **overrides: Any) -> ParagraphStyle:
    """Upright-Helvetica ParagraphStyle for the given scale name.

    Every scale entry — including ``caption`` — uses upright Helvetica or
    Helvetica-Bold, replacing reportlab's oblique Heading3/Heading4 defaults
    and its typically-italic Caption default (tests/unit/test_pdf_theme.py
    ``test_para_faces_are_upright_helvetica`` pins this across the whole scale).
    """
    base = dict(_SCALE[style_name])
    base.setdefault("alignment", TA_LEFT)
    base.update(overrides)
    return ParagraphStyle(f"pdf_{style_name}", **base)


def table_style(*, numeric_cols: list[int] | None = None, total_row: bool = False) -> TableStyle:
    """The one shared table style — surface-2 header, border-subtle rules,
    zebra alt-rows, right-aligned numeric columns."""
    cmds: list[tuple[Any, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), PDFColors.surface2),
        # Important-1 (final-review): ink2 (not ink3) — ink3 was sub-WCAG-AA
        # (~2.3:1 on surface2). Only affects plain-string header rows; this
        # TEXTCOLOR command is inert on Paragraph-cell headers (see
        # pdf_theme.para("table_header") for those).
        ("TEXTCOLOR", (0, 0), (-1, 0), PDFColors.ink2),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 1), (-1, -1), PDFColors.ink1),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, PDFColors.border_subtle),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, PDFColors.border_strong),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PDFColors.surface2]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for c in numeric_cols or []:
        cmds.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    if total_row:
        cmds.append(("BACKGROUND", (0, -1), (-1, -1), PDFColors.surface2))
        cmds.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    return TableStyle(cmds)


# ---- Brand logomark (T3, #59) ----

# Source-of-truth SVG (src/idraa/templates/macros/logo.html), 0 0 32 32 viewBox:
#   <path d="M3 7 C 11 8, 12 24, 29 26" fill="none" stroke="currentColor"
#         stroke-width="2.6" stroke-linecap="round"/>
#   <path d="M3 7 C 11 8, 12 24, 29 26 L 29 29 L 3 29 Z" fill="currentColor"
#         opacity=".14"/>
#   <circle cx="3" cy="7" r="2.6" fill="currentColor"/>
_LOGOMARK_VIEWBOX = 32.0


def brand_logomark(width: float = 22.0) -> Drawing:
    """Reportlab port of the deck logomark (macros/logo.html's inline SVG).

    Three shapes, scaled from the 32x32 SVG viewBox to ``width``: the curve
    stroke, a translucent fill wedge under the curve, and the leading dot.

    CRITICAL: reportlab's Drawing origin is BOTTOM-left (Y-up); SVG's is
    TOP-left (Y-down). Every viewBox coordinate is mapped through
    ``y' = 32 - y`` (applied in viewBox space, before scaling) — skipping
    this mirrors the curve vertically.
    """
    scale = width / _LOGOMARK_VIEWBOX

    def sx(x: float) -> float:
        return x * scale

    def sy(y: float) -> float:
        return (_LOGOMARK_VIEWBOX - y) * scale

    fill_translucent = Color(
        PDFColors.brand.red, PDFColors.brand.green, PDFColors.brand.blue, alpha=0.14
    )

    d = Drawing(width, width)

    # Shape 1: curve stroke, no fill.
    curve = Path(
        strokeColor=PDFColors.brand,
        strokeWidth=2.6 * scale,
        strokeLineCap=1,  # round
        fillColor=None,
    )
    curve.moveTo(sx(3), sy(7))
    curve.curveTo(sx(11), sy(8), sx(12), sy(24), sx(29), sy(26))
    d.add(curve)

    # Shape 2: translucent fill wedge under the curve, no stroke.
    wedge = Path(strokeColor=None, fillColor=fill_translucent)
    wedge.moveTo(sx(3), sy(7))
    wedge.curveTo(sx(11), sy(8), sx(12), sy(24), sx(29), sy(26))
    wedge.lineTo(sx(29), sy(29))
    wedge.lineTo(sx(3), sy(29))
    wedge.closePath()
    d.add(wedge)

    # Shape 3: leading dot.
    dot = Circle(sx(3), sy(7), 2.6 * scale, fillColor=PDFColors.brand, strokeColor=None)
    d.add(dot)

    return d
