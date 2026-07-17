"""Regression for issue #158: runs/_status_poll.html CSRF header + field name typos.

Pre-fix the template emitted:
- ``hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'`` — wrong header
  name (middleware expects ``X-CSRF-Token`` with hyphen per
  middleware/csrf.py:59 ``CSRF_HEADER_NAME``).
- ``<input type="hidden" name="csrf_token" value="{{ csrf_token }}">`` —
  wrong form-field name (middleware expects ``_csrf`` per
  middleware/csrf.py:58 ``CSRF_FORM_FIELD``).

Both name mismatches meant the Cancel + Re-run buttons would fail CSRF
with 403 if ever clicked. They weren't on any current UAT path, so
the bug was latent — surfaced by sweep during #157's sibling-fix audit.

The 6 occurrences are 2 per form across 3 forms (Cancel running-run,
Re-run failed-run, Re-run cancelled-run). Source-level assertion: the
template file must contain only the correct names, never the typoed
ones. A character-level assertion is the most direct regression and
will fire if anyone re-introduces the typo via copy-paste.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "idraa"
    / "templates"
    / "runs"
    / "_status_poll.html"
)


@pytest.fixture(scope="module")
def template_source() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def test_status_poll_uses_correct_csrf_header_name(template_source: str) -> None:
    """All hx-headers must use ``X-CSRF-Token`` (with hyphen), never ``X-CSRFToken``."""
    assert "X-CSRFToken" not in template_source, (
        "Found legacy 'X-CSRFToken' (no hyphen). Middleware expects "
        "'X-CSRF-Token' (CSRF_HEADER_NAME at middleware/csrf.py:59). "
        "Rename in all hx-headers attrs in runs/_status_poll.html."
    )
    assert template_source.count("X-CSRF-Token") == 3, (
        "Expected 3 'X-CSRF-Token' occurrences (one per hx-post form: "
        "Cancel, Re-run failed, Re-run cancelled). "
        f"Found {template_source.count('X-CSRF-Token')}."
    )


def test_status_poll_uses_correct_csrf_form_field_name(template_source: str) -> None:
    """All hidden CSRF inputs must use ``name="_csrf"``, never ``name="csrf_token"``."""
    assert 'name="csrf_token"' not in template_source, (
        "Found legacy hidden input name='csrf_token'. Middleware expects "
        "name='_csrf' (CSRF_FORM_FIELD at middleware/csrf.py:58). "
        "Rename in all <input type='hidden'> CSRF tokens in runs/_status_poll.html."
    )
    assert template_source.count('name="_csrf"') == 3, (
        "Expected 3 name='_csrf' hidden inputs (one per form: Cancel, "
        "Re-run failed, Re-run cancelled). "
        f"Found {template_source.count('name=' + chr(34) + '_csrf' + chr(34))}."
    )
