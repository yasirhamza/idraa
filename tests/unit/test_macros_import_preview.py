"""Mobile tranche 2e: the import_preview card-stacking macro.

``preview_table`` renders BOTH a desktop <table> (hidden md:block) and a mobile
card stack (md:hidden) for the two-step import preview pages, with an optional
action-badge column pulled to the top of each mobile card.
"""

from __future__ import annotations

from idraa.app import templates


def _render(rows: list[dict], columns: list[dict], action_key: str | None = None) -> str:
    src = (
        "{% from 'macros/import_preview.html' import preview_table %}"
        "{{ preview_table(rows, columns, action_key=action_key) }}"
    )
    return templates.env.from_string(src).render(rows=rows, columns=columns, action_key=action_key)


def _preview_cols() -> list[dict]:
    return [
        {"label": "Line", "key": "line", "mono": True},
        {"label": "Name", "key": "name"},
        {"label": "Action", "key": "action"},
    ]


def test_renders_both_desktop_table_and_mobile_cards() -> None:
    html = _render(
        [{"line": 2, "name": "Ransomware", "action": "create"}],
        _preview_cols(),
        action_key="action",
    )
    # Desktop: a real table hidden on phones.
    assert "hidden md:block" in html
    assert "<table" in html
    # Mobile: a card stack hidden from md up.
    assert "md:hidden" in html
    assert "<article" in html
    assert "<dl" in html
    # Both surfaces show the data.
    assert "Ransomware" in html


def test_action_values_map_to_badge_classes() -> None:
    rows = [
        {"line": 1, "name": "a", "action": "create"},
        {"line": 2, "name": "b", "action": "skip"},
        {"line": 3, "name": "c", "action": "error"},
    ]
    html = _render(rows, _preview_cols(), action_key="action")
    assert "badge-success" in html  # create
    assert "badge-ghost" in html  # skip
    assert "badge-error" in html  # error


def test_add_and_update_actions_map_to_badge_classes() -> None:
    # ``add`` (library bundle) and ``update`` (overlays) are in the shared map.
    html = _render(
        [
            {"line": 1, "name": "a", "action": "add"},
            {"line": 2, "name": "b", "action": "update"},
        ],
        _preview_cols(),
        action_key="action",
    )
    assert "badge-success" in html  # add
    assert "badge-info" in html  # update


def test_action_column_not_duplicated_as_dl_pair_on_mobile() -> None:
    """The action column is rendered as a top-right badge on each mobile card,
    NOT also as an "Action" <dt>/<dd> pair (which would be redundant)."""
    html = _render(
        [{"line": 1, "name": "a", "action": "create"}],
        _preview_cols(),
        action_key="action",
    )
    # The "Action" header still appears in the desktop <th>, but it must not be
    # emitted as a mobile <dt> label.
    assert '<dt class="text-meta text-ink-2">Action</dt>' not in html


def test_parked_and_duplicate_actions_map_to_badge_classes() -> None:
    """Epic #34 P1c Task 6 (plan-gate Spec-R2-NTH): the register-import
    preview page classifies rows into would_create/parked/duplicates/errors
    (service-side bucket names), rendered here under the badge keys
    create/parked/duplicate/error. ``parked`` and ``duplicate`` are new
    keys added by this task — assert they resolve to a real badge class
    rather than falling through to the map's empty-string default."""
    rows = [
        {"line": 1, "name": "a", "action": "parked"},
        {"line": 2, "name": "b", "action": "duplicate"},
    ]
    html = _render(rows, _preview_cols(), action_key="action")
    assert "badge-ghost" in html  # parked
    assert "badge-warning" in html  # duplicate


def test_errors_table_without_action_key_has_no_badge() -> None:
    """The validation-errors table passes no action_key, so no badge renders and
    every column (including the free-text Reason) becomes a card field."""
    html = _render(
        [{"line": 7, "column": "tef_low", "reason": "low must be ≤ mode"}],
        [
            {"label": "Line", "key": "line", "mono": True},
            {"label": "Column", "key": "column", "mono": True},
            {"label": "Reason", "key": "reason"},
        ],
    )
    assert "badge" not in html
    assert "low must be ≤ mode" in html
    # The reason still appears as a mobile card field.
    assert '<dt class="text-meta text-ink-2">Reason</dt>' in html


def test_missing_optional_key_renders_empty_not_error() -> None:
    """A row missing an optional column key renders empty (Jinja Undefined), not
    a render error — preview rows omit e.g. ``name`` on some error rows."""
    html = _render(
        [{"line": 9, "action": "error"}],  # no "name" key
        _preview_cols(),
        action_key="action",
    )
    assert "badge-error" in html
