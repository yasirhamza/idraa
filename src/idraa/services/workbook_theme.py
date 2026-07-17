"""Design-system token palette for the xlsxwriter verification workbook.

The Excel analogue of ``services/pdf_theme.py``'s ``PDFColors``: the single
source of the workbook's colors, mapped to the app.css ``:root`` light tokens so
the web app, PDF report, and Excel workbook render one visual system.

xlsxwriter format dicts need literal ``#RRGGBB`` strings (no CSS vars, no
HexColor objects), so these are plain strings.
"""

from __future__ import annotations


class WorkbookColors:
    """Design tokens (app.css :root light) as xlsxwriter ``#RRGGBB`` literals."""

    # Brand + ink
    brand = "#0F4C81"
    ink1 = "#18181B"
    ink2 = "#52525B"
    ink3 = "#A1A1AA"
    white = "#FFFFFF"
    # Surfaces + borders
    surface1 = "#FFFFFF"
    surface2 = "#F4F4F5"
    border_subtle = "#E4E4E7"
    # Status (text-weight, -700)
    status_success = "#15803D"
    status_warning = "#B45309"
    status_critical = "#B91C1C"
    # Verdict fills — light -100 tints of the status text colors (the design's
    # alert background/content pairing: light fill + dark status text).
    success_fill = "#DCFCE7"
    warning_fill = "#FEF3C7"
