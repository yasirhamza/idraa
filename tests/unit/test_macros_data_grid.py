"""F12: data_grid macro — sticky axes, zebra rows, totals row, mobile fallback,
density variant, sortable headers, header popover, cell tooltips (M-4 framing),
row-label action menu (SC-3)."""

from __future__ import annotations

from types import SimpleNamespace

from idraa.app import templates


def _render(**kwargs) -> str:
    src = "{% from 'macros/data_grid.html' import data_grid %}{{ data_grid(**kw) }}"
    return templates.env.from_string(src).render(kw=kwargs)


def _matrix() -> SimpleNamespace:
    """Plan-gate M-2: cells is list[{control_id, value: float|None}], dict-per-cell."""
    return SimpleNamespace(
        controls=[
            SimpleNamespace(
                control_id="av-edr",
                control_name="AV/EDR rollout",
                control_type="Detective",
                total_reduction=412000,
            ),
            SimpleNamespace(
                control_id="backups",
                control_name="Backups",
                control_type="Recovery",
                total_reduction=1240000,
            ),
            SimpleNamespace(
                control_id="email-gw",
                control_name="Email gateway",
                control_type="Preventive",
                total_reduction=890000,
            ),
        ],
        rows=[
            SimpleNamespace(
                scenario_name="Ransomware-DC",
                scenario_id="r1",
                cells=[
                    {"control_id": "av-edr", "value": 412000},
                    {"control_id": "backups", "value": None},
                    {"control_id": "email-gw", "value": None},
                ],
            ),
            SimpleNamespace(
                scenario_name="Phishing → BEC",
                scenario_id="r2",
                cells=[
                    {"control_id": "av-edr", "value": None},
                    {"control_id": "backups", "value": 40000},
                    {"control_id": "email-gw", "value": 890000},
                ],
            ),
            SimpleNamespace(
                scenario_name="Insider data exfil",
                scenario_id="r3",
                cells=[
                    {"control_id": "av-edr", "value": None},
                    {"control_id": "backups", "value": 1200000},
                    {"control_id": "email-gw", "value": None},
                ],
            ),
        ],
    )


def test_data_grid_renders_sticky_corner_first_col_and_thead() -> None:
    html = _render(matrix=_matrix(), row_label_header="Scenario")
    assert "sticky" in html
    assert "Scenario" in html
    assert "left-0" in html  # sticky-left
    for n in ("AV/EDR rollout", "Backups", "Email gateway"):
        assert n in html


def test_data_grid_renders_each_row_label_and_values() -> None:
    html = _render(matrix=_matrix(), row_label_header="Scenario")
    assert "Ransomware-DC" in html
    assert "Phishing → BEC" in html
    # Money values rendered via abbreviate_money filter:
    # 412000 → $412k, 1200000 → $1.20M
    assert "$412k" in html or "412" in html
    assert "$1.20M" in html or "$1.2M" in html or "1,200,000" in html


def test_data_grid_renders_zebra_striping() -> None:
    html = _render(matrix=_matrix(), row_label_header="Scenario")
    assert "even:bg-surface-2" in html or "even:" in html


def test_data_grid_totals_row_on_when_show_totals_true() -> None:
    html = _render(matrix=_matrix(), row_label_header="Scenario", show_totals=True)
    # Per-control totals appear in the footer
    assert "412" in html
    assert "1.24M" in html or "1,240" in html or "$1.24M" in html
    assert "Total per control" in html or "Total" in html


def test_data_grid_uses_content_driven_sizing() -> None:
    """Removed 2026-05-23 per user feedback: previously density='dense' switched
    rows to h-8/w-24 and default to h-9/w-28. Content-driven autofit produces
    better results without manual tuning. Regression guard: cells must NOT carry
    the hard-pinned height/width classes from either density variant."""
    html_default = _render(matrix=_matrix(), row_label_header="Scenario")
    html_dense = _render(matrix=_matrix(), row_label_header="Scenario", density="dense")
    for html in (html_default, html_dense):
        for cls in (" h-8 ", " h-9 ", " w-24 ", " w-28 "):
            assert cls not in html, f"Hard-pinned size class {cls.strip()} regressed"


def test_data_grid_renders_column_subscript_when_provided() -> None:
    """Plan-gate M-3: column_subscript appears under each column header (Shapley $)."""
    html = _render(matrix=_matrix(), row_label_header="Scenario", column_subscript="(Shapley $)")
    assert "(Shapley $)" in html


def test_data_grid_sortable_headers_emit_htmx_when_sort_url_set() -> None:
    """Plan-gate SC-3: column headers become HTMX sort links when sort_url is provided."""
    html = _render(
        matrix=_matrix(),
        row_label_header="Scenario",
        sort_url="/runs/abc/matrix",
        sort_by="av-edr",
        sort_dir="desc",
    )
    assert "hx-get" in html
    assert "sort=av-edr" in html
    assert "↓" in html


def test_data_grid_sort_button_hx_target_resolves_to_real_ancestor() -> None:
    """D5 (#266): the sort button's ``hx-target="closest <selector>"`` must resolve
    to a genuine ANCESTOR of the button. HTMX's ``closest`` walks up from the element
    (inclusive) and matches the nearest ancestor — if no ancestor matches, the swap
    target is nothing and the sort silently no-ops.

    The button lives in the ``<thead>``; the original ``closest .data-grid-table``
    pointed at a class on the ``<tbody>``, which is a SIBLING of the thead, not an
    ancestor — so it resolved to nothing. This test fails on the broken form and
    passes once the target points at a real ancestor (the ``<table>``).

    Out of scope: the swap-fragment contract (what the endpoint returns / swap mode).
    There is no live caller yet; this only asserts the target SELECTOR is valid.
    """
    from bs4 import BeautifulSoup

    html = _render(
        matrix=_matrix(),
        row_label_header="Scenario",
        sort_url="/runs/abc/matrix",
        sort_by="av-edr",
        sort_dir="desc",
    )
    soup = BeautifulSoup(html, "html.parser")

    sort_buttons = [
        b for b in soup.find_all("button") if b.get("hx-target", "").startswith("closest ")
    ]
    assert sort_buttons, "expected at least one sort button with an hx-target"

    for button in sort_buttons:
        target = button["hx-target"]
        assert target.startswith("closest "), target
        selector = target[len("closest ") :].strip()

        # HTMX `closest` = nearest matching ancestor (inclusive of self). Walk parents
        # and confirm at least one matches the selector. find_parent(select_one) would
        # be ideal but bs4's find_parent takes name/attrs, so we walk + select against
        # each ancestor explicitly.
        matched_ancestor = None
        for ancestor in button.parents:
            if ancestor is None or ancestor.name is None:
                continue
            # Does this single ancestor element itself satisfy the selector?
            if _element_matches(ancestor, selector):
                matched_ancestor = ancestor
                break

        assert matched_ancestor is not None, (
            f"hx-target {target!r} resolves to no ancestor of the sort button; "
            f"`closest {selector}` matches nothing in the rendered grid"
        )


def _element_matches(element, selector: str) -> bool:
    """True if `element` itself satisfies the CSS `selector`.

    Minimal matcher for the selector forms this macro emits: a bare tag name
    (e.g. ``table``), a single class (e.g. ``.data-grid-table``), or
    tag.class. Self-matching is intentional — bs4's ``select`` only searches an
    element's descendants, never the element itself, so it cannot answer "does
    THIS node match?" which is exactly what HTMX's inclusive ``closest`` needs.
    """
    from bs4 import Tag

    if not isinstance(element, Tag):
        return False
    selector = selector.strip()
    if selector.startswith("."):
        return selector[1:] in (element.get("class") or [])
    if "." in selector:
        tag, _, cls = selector.partition(".")
        return element.name == tag and cls in (element.get("class") or [])
    return element.name == selector


def test_data_grid_cell_title_carries_shapley_framing() -> None:
    """Shapley semantics (Task 5): cell tooltip references Shapley contribution and
    summability; old multiplicative/isolated framing is gone."""
    html = _render(matrix=_matrix(), row_label_header="Scenario")
    assert "Ransomware-DC" in html
    # New Shapley framing in cell tooltip
    assert "Shapley contribution" in html
    assert "cells sum to the scenario" in html
    # Old framing must be absent
    assert "controls compose multiplicatively" not in html
    assert "isolated impact" not in html
    assert "do not sum" not in html.lower()


def test_data_grid_cells_use_dict_shape_regression() -> None:
    """Plan-gate M-2: cells are dict-per-cell. Iterating raw floats would silently
    render every cell as `—` (the dict is truthy, not a number).
    abbreviate_money: 412000 → $412k, 1200000 → $1.20M, 890000 → $890k."""
    html = _render(matrix=_matrix(), row_label_header="Scenario")
    # 412k from av-edr × Ransomware-DC; $1.20M from backups × Insider; $890k from email-gw × Phishing
    assert "$412k" in html or "412" in html
    assert "$1.20M" in html or "$1.2M" in html or "1,200,000" in html
    assert "$890k" in html or "890" in html


def test_data_grid_empty_state_when_no_controls() -> None:
    matrix = SimpleNamespace(controls=[], rows=[])
    html = _render(
        matrix=matrix,
        row_label_header="Scenario",
        empty={
            "icon": "⛨",
            "title": "No controls applied",
            "body": "No scenarios in this run had mitigating controls.",
        },
    )
    assert "No controls applied" in html
    assert "<table" not in html


def test_data_grid_filter_and_drilldown_off_by_default() -> None:
    """#99/#100 are opt-in: no filter bar, no drill-down strip, no click
    handler unless the caller passes the flags."""
    html = _render(matrix=_matrix())
    assert "Filter scenarios" not in html
    assert "applyFilter" not in html
    assert "data-ctrl=" not in html
    # Row/column filter TARGETS are always present (inert without the bar).
    assert 'data-name="ransomware-dc"' in html
    assert 'data-cname="av/edr rollout"' in html


def test_data_grid_filterable_renders_debounced_imperative_filter() -> None:
    """#99: filter bar with debounced imperative apply (hidden-attr walk — no
    per-cell reactive bindings; hundreds-of-scenarios scale rule)."""
    html = _render(matrix=_matrix(), filterable=True)
    assert "Filter scenarios" in html and "Filter controls" in html
    assert "input.debounce.200ms" in html
    assert "applyFilter()" in html
    assert 'tr.hidden = q !== ""' in html


def test_data_grid_drilldown_cell_attrs_and_single_strip() -> None:
    """#100: every valued cell carries the FAIR-factor data-* payload and a
    click handler; exactly ONE docked detail strip exists (no per-cell
    popovers)."""
    m = _matrix()
    # Attach factors to one cell (view-model shape).
    m.rows[0].cells[0] = {
        "control_id": "av-edr",
        "value": 412000,
        "factors": {"tef": 0.44, "vuln": None, "pl": 0.9, "sl": 0.85},
    }
    html = _render(matrix=m, drilldown=True)
    assert 'data-tef="×0.4400"' in html
    assert 'data-vuln="—"' in html
    assert 'data-pl="×0.9000"' in html
    assert html.count("FAIR-factor multipliers") == 1  # single docked strip
    assert "cursor-pointer" in html
    # Cells WITHOUT factors degrade to em-dash attrs, never KeyError.
    assert 'data-sl="×0.8500"' in html
