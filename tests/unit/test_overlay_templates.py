"""Structure tests for overlay CRUD templates (Task C8).

Renders each template directly through the Jinja env with synthetic
contexts and asserts load-bearing structural elements are present:

- ``edit.html`` carries the hidden ``expected_version`` input (B8) and
  marks the ``tag`` field ``readonly`` when an existing overlay is being
  edited.
- ``view.html`` shows the deactivate form to admins on active overlays
  but not to analysts.
- ``list.html`` only shows the Import CSV / Create / Download template
  buttons to admins.
- ``import_preview.html`` displays the preview token (the next-step
  confirm form needs it).
- All form-bearing templates emit ``name="_csrf"`` (via ``csrf_field()``).

Templates are rendered with a ``SimpleNamespace`` fake request that
carries a ``state.csrf_token`` so the ``csrf_field()`` Jinja global has
something to interpolate. This is the same pattern
``tests/integration/test_csrf_integration.py`` uses for the
``csrf_field`` direct-render check.
"""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from typing import Any

from idraa.app import templates
from idraa.models.enums import UserRole


def _fake_request(token: str = "test-csrf-token") -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(csrf_token=token, maintenance_badge_count=0),
        url=SimpleNamespace(path="/overlays"),
    )


def _admin_user() -> SimpleNamespace:
    return SimpleNamespace(
        email="admin@example.com",
        role=UserRole.ADMIN,
    )


def _analyst_user() -> SimpleNamespace:
    return SimpleNamespace(
        email="analyst@example.com",
        role=UserRole.ANALYST,
    )


def _fake_overlay(*, version: int = 3, is_active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
        tag="critical_infrastructure",
        display_name="Critical Infrastructure",
        frequency_multiplier=1.5,
        magnitude_multiplier=2.0,
        sources=["docs/reference/cisa-2024.pdf"],
        methodology="A sufficiently long methodology explanation for tests.",
        version=version,
        is_active=is_active,
    )


def _render(template_name: str, **ctx: Any) -> str:
    return templates.env.get_template(template_name).render(
        request=_fake_request(),
        **ctx,
    )


# ---- edit.html -------------------------------------------------------


def test_edit_html_renders_hidden_expected_version_for_existing_overlay() -> None:
    """B8 invariant: edit form must carry ``expected_version`` for the optimistic-lock check."""
    overlay = _fake_overlay(version=7)
    html = _render(
        "overlays/edit.html",
        current_user=_admin_user(),
        flash=None,
        form={
            "tag": overlay.tag,
            "display_name": overlay.display_name,
            "frequency_multiplier": overlay.frequency_multiplier,
            "magnitude_multiplier": overlay.magnitude_multiplier,
            "sources": "; ".join(overlay.sources),
            "methodology": overlay.methodology,
            "methodology_change_reason": "",
        },
        overlay=overlay,
        action=f"/overlays/{overlay.id}/edit",
        errors=[],
    )
    assert 'name="expected_version"' in html
    assert 'value="7"' in html


def test_edit_html_marks_tag_readonly_when_editing_existing_overlay() -> None:
    """Tag rename is forbidden — UI must mark the field readonly on edit."""
    overlay = _fake_overlay()
    html = _render(
        "overlays/edit.html",
        current_user=_admin_user(),
        flash=None,
        form={
            "tag": overlay.tag,
            "display_name": overlay.display_name,
            "frequency_multiplier": overlay.frequency_multiplier,
            "magnitude_multiplier": overlay.magnitude_multiplier,
            "sources": "; ".join(overlay.sources),
            "methodology": overlay.methodology,
            "methodology_change_reason": "",
        },
        overlay=overlay,
        action=f"/overlays/{overlay.id}/edit",
        errors=[],
    )
    # Tighten: the ``readonly`` attribute must appear on the tag input
    # element itself, not merely somewhere in the document. A future
    # refactor that moves ``readonly`` onto a different field (or drops
    # it entirely) must fail this test.
    match = re.search(r'<input[^>]*\bname="tag"[^>]*>', html)
    assert match is not None, "tag input not found in rendered HTML"
    assert "readonly" in match.group(0), (
        f"readonly attribute not on the tag input: {match.group(0)!r}"
    )


def test_edit_html_does_not_mark_tag_readonly_for_new_overlay() -> None:
    """New-overlay form must allow tag entry."""
    html = _render(
        "overlays/edit.html",
        current_user=_admin_user(),
        flash=None,
        form={
            "tag": "",
            "display_name": "",
            "frequency_multiplier": "1.0",
            "magnitude_multiplier": "1.0",
            "sources": "",
            "methodology": "",
            "methodology_change_reason": "",
        },
        overlay=None,
        action="/overlays",
        errors=[],
    )
    assert 'name="tag"' in html
    # No expected_version for create.
    assert 'name="expected_version"' not in html
    # Tag field should NOT be readonly. Search for the specific tag-input
    # block: the tag input is the first one with name="tag".
    # Assert pattern attribute is present (only set on the create branch).
    assert 'pattern="[a-z][a-z0-9_]*"' in html


# ---- view.html -------------------------------------------------------


def test_view_html_shows_deactivate_form_for_admin_on_active_overlay() -> None:
    overlay = _fake_overlay(is_active=True)
    html = _render(
        "overlays/view.html",
        current_user=_admin_user(),
        flash=None,
        overlay=overlay,
    )
    assert f"/overlays/{overlay.id}/deactivate" in html
    assert 'name="reason"' in html


def test_view_html_hides_deactivate_form_for_analyst() -> None:
    overlay = _fake_overlay(is_active=True)
    html = _render(
        "overlays/view.html",
        current_user=_analyst_user(),
        flash=None,
        overlay=overlay,
    )
    assert f"/overlays/{overlay.id}/deactivate" not in html


def test_view_html_hides_deactivate_form_when_overlay_already_inactive() -> None:
    overlay = _fake_overlay(is_active=False)
    html = _render(
        "overlays/view.html",
        current_user=_admin_user(),
        flash=None,
        overlay=overlay,
    )
    assert f"/overlays/{overlay.id}/deactivate" not in html


# ---- list.html -------------------------------------------------------


def test_list_html_shows_admin_actions_for_admin() -> None:
    """PR 5 moved the import form off the list page to its own /overlays/import
    route. The list page now carries an Export CSV button and an admin-only
    "+ New overlay" link. Assert the new macro layout instead of the old inline form."""
    html = _render(
        "overlays/list.html",
        current_user=_admin_user(),
        flash=None,
        overlays=[],
    )
    # Admin-gated create link present on the list page.
    assert 'href="/overlays/new"' in html
    # CSV template download button present (page_header actions).
    assert 'href="/overlays/template.csv"' in html
    # Export CSV button present (page_header actions or data_table exportable).
    assert "/overlays/export.csv" in html
    # The inline import form is NOT on the list page — it lives at /overlays/import.
    assert 'action="/overlays/import"' not in html


def test_list_html_hides_import_form_for_analyst() -> None:
    """The inline import form is gone from the list page (moved to /overlays/import
    route in PR 5). Analysts should never see an import form on this page."""
    html = _render(
        "overlays/list.html",
        current_user=_analyst_user(),
        flash=None,
        overlays=[],
    )
    # The inline import form is NOT on the list page — it has its own route.
    assert 'action="/overlays/import"' not in html


# ---- import_preview.html --------------------------------------------


def test_import_preview_html_renders_token_in_hidden_input() -> None:
    """Token must round-trip into the confirm form so step 2 can read it."""
    token = "abc123-token-value"
    html = _render(
        "overlays/import_preview.html",
        current_user=_admin_user(),
        flash=None,
        token=token,
        preview=[],
        errors=[],
    )
    assert token in html
    assert 'name="token"' in html


# ---- csrf coverage --------------------------------------------------


def test_all_form_bearing_templates_render_csrf_field() -> None:
    """Every POST-form-bearing overlay template must emit name="_csrf"."""
    overlay = _fake_overlay()
    # Annotate as ``dict[str, Any]`` so ``_render(tmpl_name, **ctx)`` typechecks
    # under mypy strict — without it, the inferred ``dict[str, object]`` value
    # type is not a valid mapping for ``**``-spread into ``**ctx: Any``.
    cases: list[tuple[str, dict[str, Any]]] = [
        (
            "overlays/list.html",
            {
                "current_user": _admin_user(),
                "flash": None,
                "overlays": [],
            },
        ),
        (
            "overlays/view.html",
            {
                "current_user": _admin_user(),
                "flash": None,
                "overlay": overlay,
            },
        ),
        (
            "overlays/edit.html",
            {
                "current_user": _admin_user(),
                "flash": None,
                "form": {
                    "tag": overlay.tag,
                    "display_name": overlay.display_name,
                    "frequency_multiplier": overlay.frequency_multiplier,
                    "magnitude_multiplier": overlay.magnitude_multiplier,
                    "sources": "",
                    "methodology": overlay.methodology,
                    "methodology_change_reason": "",
                },
                "overlay": overlay,
                "action": f"/overlays/{overlay.id}/edit",
                "errors": [],
            },
        ),
        (
            "overlays/import_preview.html",
            {
                "current_user": _admin_user(),
                "flash": None,
                "token": "tok",
                "preview": [
                    {
                        "line": 2,
                        "tag": "x",
                        "display_name": "X",
                        "frequency_multiplier": 1.0,
                        "magnitude_multiplier": 1.0,
                        "action": "create",
                    }
                ],
                "errors": [],
            },
        ),
    ]
    for tmpl_name, ctx in cases:
        html = _render(tmpl_name, **ctx)
        assert 'name="_csrf"' in html, f"{tmpl_name} missing csrf_field()"
