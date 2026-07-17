"""F9-F11: form_field + form_error_summary + kpi_card macros."""

from __future__ import annotations

from idraa.app import templates


def _r(macro_path: str, macro_name: str, **kwargs) -> str:
    src = f"{{% from '{macro_path}' import {macro_name} %}}{{{{ {macro_name}(**kw) }}}}"
    return templates.env.from_string(src).render(kw=kwargs)


def test_form_field_text_renders_label_input_help() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="org_name",
        label="Organization name",
        input_type="text",
        value="Acme",
        help="Shown on reports.",
    )
    assert "Organization name" in html
    assert 'name="org_name"' in html
    assert 'value="Acme"' in html
    assert "Shown on reports." in html


def test_form_field_renders_required_chip_not_asterisk() -> None:
    """Spec §6: REQUIRED chip, never `*`."""
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="x",
        label="X",
        input_type="text",
        required=True,
    )
    assert "REQUIRED" in html
    # No raw asterisk in the visible chrome
    # (aria-required is an HTML attr; * outside attrs is forbidden)
    assert "*" not in html.replace('aria-required="true"', "")


def test_form_field_money_includes_dollar_prefix() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="ale_ceiling",
        label="ALE ceiling",
        input_type="money",
        value="10000000",
    )
    assert "$" in html
    assert 'type="number"' in html
    assert 'inputmode="decimal"' in html


def test_form_field_percent_includes_percent_suffix() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="vuln",
        label="Vulnerability",
        input_type="percent",
    )
    assert "%" in html


def test_form_field_select_emits_options() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="kind",
        label="Kind",
        input_type="select",
        value="prev",
        options=[("prev", "Preventive"), ("det", "Detective")],
    )
    assert "<select" in html
    assert "Preventive" in html
    assert 'value="prev" selected' in html or 'value="prev"  selected' in html


def test_form_field_error_renders_red_and_replaces_help() -> None:
    """Spec §6: error replaces help; status-critical class on input."""
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="x",
        label="X",
        input_type="text",
        help="This is help.",
        error="Must be positive.",
    )
    assert "Must be positive." in html
    assert "status-critical" in html
    assert "This is help." not in html


def test_form_field_disabled_renders_disabled_attr() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="x",
        label="X",
        input_type="text",
        disabled=True,
    )
    assert "disabled" in html


def test_form_field_display_mode_renders_label_and_value_no_input() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="x",
        label="Name",
        input_type="text",
        value="Acme",
        mode="display",
    )
    assert "Name" in html
    assert "Acme" in html
    assert "<input" not in html
    assert "<select" not in html


def test_form_field_textarea_renders() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="desc",
        label="Description",
        input_type="textarea",
        value="Some text",
        rows=5,
    )
    assert "<textarea" in html
    assert "Some text" in html


def test_form_field_focus_ring_brand_class_present() -> None:
    """Spec §1: focus ring uses --color-brand."""
    html = _r("macros/form_field.html", "form_field", name="x", label="X", input_type="text")
    assert "focus:ring-brand" in html


def test_form_field_date_renders_native_date_input() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="due_on",
        label="Due",
        input_type="date",
        value="2026-06-01",
    )
    assert 'type="date"' in html
    assert 'value="2026-06-01"' in html


def test_form_field_toggle_renders_checkbox_with_toggle_class() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="enabled",
        label="Enabled",
        input_type="toggle",
        value=True,
    )
    assert 'type="checkbox"' in html
    assert "toggle" in html
    assert "checked" in html


def test_form_field_toggle_unchecked_when_value_false() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="enabled",
        label="Enabled",
        input_type="toggle",
        value=False,
    )
    assert 'type="checkbox"' in html
    assert "checked" not in html


def test_form_field_radio_group_renders_options() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="mode",
        label="Simulation mode",
        input_type="radio_group",
        value="enhanced",
        options=[
            ("baseline", "Baseline", "FAIR baseline only"),
            ("enhanced", "Enhanced", "Apply control effects"),
        ],
    )
    assert 'type="radio"' in html
    assert 'name="mode"' in html
    assert "Baseline" in html and "Enhanced" in html
    assert "Apply control effects" in html
    assert 'value="enhanced" checked' in html


def test_form_field_checkbox_group_renders_options() -> None:
    html = _r(
        "macros/form_field.html",
        "form_field",
        name="domains",
        label="Domains",
        input_type="checkbox_group",
        value=["V", "R"],
        options=[("V", "Vulnerability"), ("R", "Response"), ("T", "Threat")],
    )
    assert html.count('type="checkbox"') == 3
    assert 'value="V" checked' in html
    assert 'value="R" checked' in html
    assert 'value="T" checked' not in html


# ---------------------------------------------------------------------------
# F11 — form_error_summary + kpi_card
# ---------------------------------------------------------------------------


def test_form_error_summary_lists_each_field_with_anchor() -> None:
    html = _r(
        "macros/form_error_summary.html",
        "form_error_summary",
        errors={"name": "Must not be empty.", "ale_ceiling": "Must be positive."},
    )
    assert "Must not be empty." in html
    assert "Must be positive." in html
    assert 'href="#name"' in html
    assert 'href="#ale_ceiling"' in html


def test_form_error_summary_hidden_when_no_errors() -> None:
    html = _r("macros/form_error_summary.html", "form_error_summary", errors={})
    # Either entirely empty or contains nothing user-visible
    assert "Please fix" not in html
    assert "<li" not in html


def test_kpi_card_renders_value_with_label_money_format() -> None:
    html = _r(
        "macros/kpi_card.html",
        "kpi_card",
        label="Annual loss exposure",
        value=12345678,
        format="money",
    )
    assert "Annual loss exposure" in html
    # money is "$X,XXX,XXX" — accept any of these markers
    assert "$12,345,678" in html or "12,345,678" in html or "12.3M" in html


def test_kpi_card_renders_percent_format() -> None:
    html = _r("macros/kpi_card.html", "kpi_card", label="Coverage", value=0.873, format="percent")
    assert "87.3%" in html or "87%" in html


def test_kpi_card_renders_count_format_with_abbreviation() -> None:
    html = _r("macros/kpi_card.html", "kpi_card", label="Total runs", value=12500, format="count")
    # format_count abbreviates >=10k as "<X.X>k"
    assert "12.5k" in html


def test_kpi_card_renders_delta_with_status_colour_negative() -> None:
    html = _r(
        "macros/kpi_card.html",
        "kpi_card",
        label="Reduction",
        value=0.42,
        format="percent",
        delta=-0.085,
        delta_format="pct",
    )
    # Signed string with minus / dash glyph
    assert "8.5%" in html or "-8.5%" in html or "−8.5%" in html
    assert "numeric-neg" in html


def test_kpi_card_renders_delta_with_status_colour_positive() -> None:
    html = _r(
        "macros/kpi_card.html",
        "kpi_card",
        label="Reduction",
        value=0.42,
        format="percent",
        delta=0.085,
        delta_format="pct",
    )
    assert "8.5%" in html or "+8.5%" in html
    assert "numeric-pos" in html


def test_kpi_card_handles_none_value() -> None:
    html = _r("macros/kpi_card.html", "kpi_card", label="Unknown", value=None, format="money")
    assert "—" in html
