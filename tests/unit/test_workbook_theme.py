from __future__ import annotations

import re

from idraa.services.workbook_theme import WorkbookColors


def test_workbook_colors_match_canonical_design_tokens() -> None:
    """Pin the workbook palette to the app.css :root light tokens (same values
    the PDF's PDFColors uses), so the Excel/PDF/web surfaces stay one system."""
    assert WorkbookColors.brand == "#37464F"
    assert WorkbookColors.ink1 == "#18181B"
    assert WorkbookColors.ink2 == "#52525B"
    assert WorkbookColors.white == "#FFFFFF"
    assert WorkbookColors.surface2 == "#F4F4F5"
    assert WorkbookColors.border_subtle == "#E4E4E7"
    assert WorkbookColors.status_success == "#15803D"
    assert WorkbookColors.status_warning == "#B45309"
    assert WorkbookColors.status_critical == "#B91C1C"
    # Light verdict-fill tints (the design's alert bg pairing with the -700 text).
    assert WorkbookColors.success_fill == "#DCFCE7"
    assert WorkbookColors.warning_fill == "#FEF3C7"


def test_all_workbook_colors_are_hex_strings() -> None:
    for name in dir(WorkbookColors):
        if name.startswith("_"):
            continue
        val = getattr(WorkbookColors, name)
        assert isinstance(val, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", val), (
            f"{name}={val!r} must be a #RRGGBB literal (xlsxwriter needs literal hex)"
        )
