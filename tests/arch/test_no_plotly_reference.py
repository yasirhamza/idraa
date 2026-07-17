"""Permanent regression guard for epic #547 P3 (final Plotly rip-out).

Plotly was fully abandoned in favor of first-party server-rendered SVG
(P1: dual LEC/EPC cards; P2: the remaining chart macros; P3: removed the
vendored bundle, the server-side ``_render_plotly_chart`` renderer, and
every "plotly" string from runtime source — comments included, so a stale
reference can't silently imply the vendor bundle still exists).

This mirrors the plan-gate exit criterion: ``grep -rniE "plotly" src/`` must
return zero. Scoped to ``src/idraa`` only (not ``tests/`` or ``docs/`` —
git history and prose describing the historical migration are fine there;
this guard exists so runtime source specifically never regresses)."""

from __future__ import annotations

import pathlib
import re

_PLOTLY_RE = re.compile(r"plotly", re.IGNORECASE)


def _src_files(root: pathlib.Path):
    yield from (root / "src" / "idraa").rglob("*")


def test_no_plotly_reference_under_src():
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    offenders = []
    for path in _src_files(root):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue  # binary asset (e.g. a vendored font) — not a text reference
        if _PLOTLY_RE.search(text):
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"'plotly' reference still present under src/idraa: {offenders}"


def test_deleted_plotly_glue_files_absent():
    """The Plotly-only JS glue and vendored bundle were deleted in #547 P3.
    Assert the FILES are gone (a filename check, not a string grep — so the
    intentional lineage comments in charts.js that name the retired exporter
    don't false-positive). Prevents an accidental re-creation slipping back in.
    """
    static = pathlib.Path(__file__).resolve().parent.parent.parent / "src" / "idraa" / "static"
    gone = [
        static / "js" / "chart_theme.js",
        static / "js" / "chart_data_export.js",
    ]
    gone += list((static / "vendor").glob("plotly-*.min.js"))
    present = [str(p) for p in gone if p.exists()]
    assert present == [], f"deleted Plotly file(s) reappeared under static/: {present}"
