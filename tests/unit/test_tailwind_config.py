"""tailwind.config.js must carry the content globs + the pattern safelist so purge does
not drop split prefix/suffix classes. String-level checks + a Python mirror of the
safelist regex (no Node)."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = (REPO / "tailwind.config.js").read_text(encoding="utf-8")

# Python mirror of the config's safelist pattern; test_pattern_literal_present_in_config
# keeps the two in sync (the exact regex source must appear in the JS config).
SPLIT_CLASS_RE = re.compile(
    r"^(text|bg|border)-(status|ink|numeric|surface|brand|border)(-[a-z0-9]+)?$"
)

# Split prefix/suffix classes that exist as a literal NOWHERE (plan-gate B2) and so MUST be
# covered by the pattern, not by content scanning. (Sources: app.py format_delta, status_pill.)
KNOWN_SPLIT_CLASSES = [
    "text-numeric-pos",
    "text-numeric-neg",
    "text-ink-2",
    "text-status-success",
    "text-status-warning",
    "text-status-critical",
    "text-status-info",
    "bg-surface-1",
    "border-border-subtle",
]


def test_content_globs_present():
    assert "./src/idraa/templates/**/*.html" in CONFIG
    assert "./src/idraa/static/js/**/*.js" in CONFIG


def test_no_py_content_glob():
    # Audited: every Python-referenced class is a DaisyUI component or already in templates,
    # so a .py scan adds no real class and only emitted junk selectors from Python slices.
    # Guard against re-adding it.
    assert "./src/idraa/**/*.py" not in CONFIG


def test_no_npm_daisyui_require():
    # DaisyUI ships as a vendored precompiled file; the build must not need it.
    assert 'require("daisyui")' not in CONFIG and "require('daisyui')" not in CONFIG


def test_pattern_literal_present_in_config():
    assert r"/^(text|bg|border)-(status|ink|numeric|surface|brand|border)(-[a-z0-9]+)?$/" in CONFIG


def test_pattern_covers_known_split_classes():
    for cls in KNOWN_SPLIT_CLASSES:
        assert SPLIT_CLASS_RE.match(cls), f"safelist pattern must cover split class {cls}"


def test_pattern_does_not_overmatch_arbitrary():
    # sanity: the pattern is scoped to the custom families, not all of Tailwind
    assert SPLIT_CLASS_RE.match("text-red-500") is None
