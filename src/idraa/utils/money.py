"""Server-side mirror of the client money sanitize (static/js/money_input.js).

Money inputs across the app are Excel-like text fields that submit
comma-grouped values (owner UAT 2026-07-31/08-01). Every parser of a money
form field routes the raw string through :func:`sanitize_money_str` before
float coercion, so benign formatting (currency symbols, spaces incl. NBSP,
commas, apostrophes, underscores) never 422s an honest entry — while
anything semantic (letters, signs, exponent suffixes) SURVIVES the strip and
fails float() loudly, per the never-launder invariant: a magnitude may be
rejected, never silently rewritten.

KEEP IN SYNC with BENIGN in static/js/money_input.js.
"""

from __future__ import annotations

import re
from typing import Any

_BENIGN = re.compile(r"[\s$€£¥,'_]")


def sanitize_money_str(value: Any) -> Any:
    """Strip benign money formatting from a form string; pass non-strings through."""
    if isinstance(value, str):
        return _BENIGN.sub("", value)
    return value
