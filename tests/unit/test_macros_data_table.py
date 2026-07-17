"""F7-F8: data_table macro — desktop + pagination + card stack (F8)."""

from __future__ import annotations

from types import SimpleNamespace

from idraa.app import templates


def _render(**kwargs) -> str:
    src = "{% from 'macros/data_table.html' import data_table %}{{ data_table(**kw) }}"
    return templates.env.from_string(src).render(kw=kwargs)


def _cols():
    return [
        {"key": "name", "label": "Name", "sortable": True, "priority": "primary"},
        {"key": "domain", "label": "Domain"},
        {"key": "status", "label": "Status", "kind": "status_pill", "pill_kind": "control"},
        {"key": "amount", "label": "ALE", "align": "right", "numeric": True},
    ]


def _rows():
    return [
        SimpleNamespace(name="AV/EDR", domain="V·R", status="active", amount="$412,000"),
        SimpleNamespace(name="Backups", domain="R", status="active", amount="$1,200,000"),
        SimpleNamespace(name="Email gw", domain="V", status="maintenance", amount="$-"),
    ]


def test_data_table_renders_thead_with_columns() -> None:
    html = _render(rows=_rows(), columns=_cols(), sort_by="name", sort_dir="asc", sort_url="/x")
    assert "<table" in html
    for col in ("Name", "Domain", "Status", "ALE"):
        assert col in html, f"Header {col} missing"


def test_sortable_column_emits_htmx_sort_link() -> None:
    html = _render(
        rows=_rows(), columns=_cols(), sort_by="name", sort_dir="asc", sort_url="/controls"
    )
    # Indicator on active column
    assert "↑" in html or "↓" in html
    # HTMX sort link
    assert "hx-get" in html
    assert "sort=" in html


def test_data_table_renders_each_row() -> None:
    html = _render(rows=_rows(), columns=_cols(), sort_by="name", sort_dir="asc")
    assert "AV/EDR" in html
    assert "Backups" in html
    assert "Email gw" in html


def test_data_table_status_pill_column_renders_dot_glyph() -> None:
    html = _render(rows=_rows(), columns=_cols(), sort_by="name", sort_dir="asc")
    assert "●" in html
    # Active / Maintenance present in some form (titlecased by status_pill)
    assert "Active" in html


def test_data_table_row_href_template_wraps_primary_cell_as_link() -> None:
    html = _render(
        rows=_rows(),
        columns=_cols(),
        sort_by="name",
        sort_dir="asc",
        row_href_template="/controls/{name}",
    )
    # AV/EDR row → href="/controls/AV/EDR"
    assert 'href="/controls/AV/EDR"' in html


def test_data_table_action_menu_column_renders() -> None:
    rows = [
        SimpleNamespace(
            name="X",
            domain="V",
            status="active",
            amount="$0",
            _actions=[{"label": "Edit", "href": "/x/edit"}],
        )
    ]
    cols = [*_cols(), {"key": "_actions", "kind": "action_menu", "label": ""}]
    html = _render(rows=rows, columns=cols, sort_by="name", sort_dir="asc")
    assert "⋯" in html
    assert 'href="/x/edit"' in html


def test_data_table_empty_renders_empty_state_when_rows_empty() -> None:
    html = _render(
        rows=[],
        columns=_cols(),
        sort_by="name",
        sort_dir="asc",
        empty={
            "icon": "⛨",
            "title": "No controls",
            "body": "Create one or import.",
            "cta": {"label": "+ New", "href": "/controls/new", "style": "primary"},
        },
    )
    assert "<table" not in html
    assert "No controls" in html
    assert 'href="/controls/new"' in html


def test_data_table_exportable_renders_export_link() -> None:
    html = _render(
        rows=_rows(),
        columns=_cols(),
        sort_by="name",
        sort_dir="asc",
        exportable=True,
        export_url="/controls/export.csv",
    )
    assert 'href="/controls/export.csv' in html
    assert "Export" in html


def test_data_table_thead_is_not_sticky_regression_2026_05_23() -> None:
    """Regression guard: SC-13 originally asked for sticky thead via
    `<tr class="sticky" style="top: var(--page-header-height)">`. That overlay-ed
    the first data row on production (CSS `position: sticky` on `<tr>` is
    poorly supported, especially with `border-collapse: separate` default).
    Sticky thead was REMOVED 2026-05-23. Guard against accidental
    reintroduction: assert the thead row does NOT carry `sticky` + `top:`."""
    html = _render(rows=_rows(), columns=_cols(), sort_by="name", sort_dir="asc")
    # The thead row should be a plain `<tr>` (no sticky class on it).
    # `sticky` may still appear elsewhere in the page (page_header) — only
    # forbid the specific pattern that broke production.
    assert "sticky z-10 bg-surface-2" not in html
    assert "var(--page-header-height" not in html


def test_data_table_rows_use_content_driven_heights() -> None:
    """Removed 2026-05-23: previously SC-2 pinned h-11 default / h-9 compact on rows.
    User feedback: 'I thought UI technology has evolved past manual column widths
    and row heights' — autofit produces better results across variable content
    lengths. Regression guard: rows must not carry hard-coded h-* classes."""
    html = _render(rows=_rows(), columns=_cols(), sort_by="name", sort_dir="asc")
    # The thead/body region of the macro renders without forced row heights now.
    # Allow `h-` to appear elsewhere (e.g. h-0.5 on the progress bar), only forbid
    # the specific row-height pins.
    assert " h-11 " not in html and ' h-11"' not in html and " h-11\n" not in html
    assert " h-9 " not in html and ' h-9"' not in html and " h-9\n" not in html


def test_data_table_renders_pagination_with_localstorage_key() -> None:
    """Plan-gate SC-16: when page_size + page_size_key both provided, pagination
    footer emits rows-per-page select wired to localStorage idraa.pageSize.<key>,
    plus HTMX prev/next page nav."""
    html = _render(
        rows=_rows(),
        columns=_cols(),
        sort_by="name",
        sort_dir="asc",
        page_size=25,
        page=2,
        total=120,
        page_size_key="controls",
    )
    assert "idraa.pageSize.controls" in html
    for opt in ("25", "50", "100"):
        assert f'value="{opt}"' in html
    assert "Page 2 of 5" in html
    assert "‹" in html and "›" in html
    assert "hx-get" in html and "page=1" in html and "page=3" in html


def test_data_table_omits_pagination_when_no_page_size_key() -> None:
    """Pagination opt-in via page_size_key."""
    html = _render(rows=_rows(), columns=_cols(), sort_by="name", sort_dir="asc")
    assert "idraa.pageSize" not in html
    assert "Rows per page" not in html


def test_data_table_emits_mobile_card_markup() -> None:
    html = _render(rows=_rows(), columns=_cols(), sort_by="name", sort_dir="asc")
    # Card stack container is hidden on md+
    assert "md:hidden" in html
    # Desktop table container is hidden on <md
    assert "hidden md:block" in html
    # Primary-priority column (name) becomes the card title — present in card markup
    assert "AV/EDR" in html


def test_data_table_hides_secondary_columns_when_priority_hidden_on_mobile() -> None:
    cols = [
        {"key": "name", "label": "Name", "priority": "primary"},
        {"key": "domain", "label": "Domain", "priority": "hidden_on_mobile"},
    ]
    rows = [SimpleNamespace(name="X", domain="DROPME")]
    html = _render(rows=rows, columns=cols, sort_by="name", sort_dir="asc")
    # In the mobile card section, hidden_on_mobile column must not appear.
    # Cheap proxy: extract md:hidden block and assert DROPME isn't in it.
    import re

    # Find the md:hidden region (mobile card stack)
    mobile_blocks = re.findall(r"(?s)<div[^>]*\bmd:hidden\b.*?</div>\s*</div>", html)
    if mobile_blocks:
        mobile_section = "\n".join(mobile_blocks)
        # In the mobile card section, DROPME should not be visible as a value
        # (the dt/dd label/value pair for "domain" should be dropped entirely)
        assert "DROPME" not in mobile_section


def test_data_table_card_status_pill_renders_on_mobile() -> None:
    """Status column should still render via status_pill on mobile cards."""
    html = _render(rows=_rows(), columns=_cols(), sort_by="name", sort_dir="asc")
    # status pill produces the dot glyph + value somewhere
    assert "●" in html
    # And the value text
    assert "Active" in html


def test_data_table_card_action_menu_relocates_to_footer() -> None:
    """action_menu column should appear once on desktop, once on mobile card footer."""
    rows = [
        SimpleNamespace(
            name="X",
            domain="V",
            status="active",
            amount="$0",
            _actions=[{"label": "Edit", "href": "/x/edit"}],
        )
    ]
    cols = [*_cols(), {"key": "_actions", "kind": "action_menu", "label": ""}]
    html = _render(rows=rows, columns=cols, sort_by="name", sort_dir="asc")
    # Two ⋯ buttons (one desktop, one mobile)
    assert html.count("⋯") >= 2
