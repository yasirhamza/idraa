"""pdf_theme is the single reportlab styling vocabulary for the PDF report.
Pins its colors to the design tokens (app.css :root) and the chart pair to
chart_palette, so PDF and web can't drift."""

from reportlab.lib import colors

from idraa.services import pdf_theme
from idraa.services.chart_palette import CHART_SERIES

# Light-mode token hexes from src/idraa/static/css/app.css :root
_TOKENS = {
    "brand": "#0F4C81",
    "ink1": "#18181B",
    "ink2": "#52525B",
    "ink3": "#A1A1AA",
    "surface0": "#FAFAF9",
    "surface1": "#FFFFFF",
    "surface2": "#F4F4F5",
    "border_subtle": "#E4E4E7",
    "border_strong": "#D4D4D8",
    "status_critical": "#B91C1C",
    "status_warning": "#B45309",
    "status_success": "#15803D",
    "status_info": "#1D4ED8",
    "numeric_pos": "#15803D",
    "numeric_neg": "#B91C1C",
}


def test_palette_matches_tokens():
    for name, hexval in _TOKENS.items():
        assert getattr(pdf_theme.PDFColors, name) == colors.HexColor(hexval), name


def test_chart_pair_is_chart_palette_light():
    assert pdf_theme.PDFColors.chart_inherent == colors.HexColor(CHART_SERIES["inherent"]["light"])
    assert pdf_theme.PDFColors.chart_residual == colors.HexColor(CHART_SERIES["residual"]["light"])
    # And the CHART dict routes them:
    assert pdf_theme.CHART["inherent"] == pdf_theme.PDFColors.chart_inherent
    assert pdf_theme.CHART["residual"] == pdf_theme.PDFColors.chart_residual


def test_para_faces_are_upright_helvetica():
    # No oblique/italic faces anywhere in the type scale.
    for name in (
        "display",
        "h1",
        "h2",
        "h3",
        "body",
        "meta",
        "micro",
        "number_lg",
        "number_md",
        "caption",
        "footer",
        "wordmark",
        "table_header",
        "table_header_right",
    ):
        st = pdf_theme.para(name)
        assert "Oblique" not in st.fontName and "Italic" not in st.fontName, (name, st.fontName)
        assert st.fontName.startswith("Helvetica"), (name, st.fontName)


def test_para_meta_is_muted_bold():
    """'meta' is ink3 + bold — no case transform. reportlab's ParagraphStyle has
    no text-transform primitive, so any uppercasing is a call-site copy
    decision, not something this style applies."""
    meta = pdf_theme.para("meta")
    assert meta.textColor == pdf_theme.PDFColors.ink3
    assert meta.fontName == "Helvetica-Bold"


def test_para_table_header_is_ink2_bold():
    """Important-1 (final-review): every table header renders identically —
    upright Helvetica-Bold, ink2 (AA-legible), 8pt."""
    head = pdf_theme.para("table_header")
    assert head.textColor == pdf_theme.PDFColors.ink2
    assert head.fontName == "Helvetica-Bold"
    assert head.fontSize == 8

    head_r = pdf_theme.para("table_header_right")
    assert head_r.textColor == pdf_theme.PDFColors.ink2
    assert head_r.fontName == "Helvetica-Bold"
    assert head_r.alignment == 2  # TA_RIGHT


def test_table_style_header_textcolor_is_ink2():
    """table_style()'s header TEXTCOLOR command must match the Paragraph-header
    'table_header' style's ink2 — otherwise plain-string-header tables and
    Paragraph-header tables render two different header colors."""
    from reportlab.platypus import TableStyle

    ts = pdf_theme.table_style()
    assert isinstance(ts, TableStyle)
    cmds = ts.getCommands()
    header_textcolor_cmds = [c for c in cmds if c[0] == "TEXTCOLOR" and c[1] == (0, 0)]
    assert header_textcolor_cmds, "no header TEXTCOLOR command found"
    assert header_textcolor_cmds[0][3] == pdf_theme.PDFColors.ink2


def test_table_style_returns_tablestyle():
    from reportlab.platypus import TableStyle

    assert isinstance(pdf_theme.table_style(numeric_cols=[1, 2, 3]), TableStyle)


def test_brand_logomark_drawing():
    """T3 (#59): brand_logomark() is the reportlab port of the deck logomark
    SVG (macros/logo.html) — curve stroke + translucent fill wedge + dot,
    scaled from the 32-unit viewBox to the requested width."""
    from reportlab.graphics.shapes import Drawing

    d = pdf_theme.brand_logomark()
    assert isinstance(d, Drawing)
    assert d.width == 22.0
    assert len(d.contents) == 3
    stroke_colors = [getattr(shape, "strokeColor", None) for shape in d.contents]
    assert pdf_theme.PDFColors.brand in stroke_colors
