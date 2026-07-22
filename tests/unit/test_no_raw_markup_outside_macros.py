"""F24 plan-gate SC-8: forbid raw <table>/<input>/<select>/<label> in templates
outside src/idraa/templates/macros/ (the macro library is the only legitimate
source). Catches regressions where a future PR introduces inline form chrome
that bypasses the design system.

The ALLOWLIST below catalogs all pre-existing violations as of the F24 cleanup
PR. Every entry carries a one-line justification. The set is intentionally
closed — new entries require an explicit justification comment here, making
scope creep visible at review time.

Mobile tranche 2e (2026-06) migrated the three import-PREVIEW templates
(scenarios/overlays/library import_preview.html) onto the new
``macros/import_preview.html`` card-stacking macro, so they no longer carry raw
<table> markup and were removed from the allowlist (the linter now actively
guards them against regressing to raw tables). The import_result.html siblings
still use raw tables and remain allowlisted.
"""

from __future__ import annotations

import re
from pathlib import Path

FORBIDDEN = re.compile(
    r"<(table|select|label)(\s|>)"
    r'|<input\b(?![^>]*\btype=["\']hidden["\'])',
    re.IGNORECASE,
)
TEMPLATES_ROOT = Path("src/idraa/templates")
ALLOWED_DIRS = {"macros"}

# Closed-list allowlist — each entry needs a one-line justification comment.
#
#   analyses/new.html
#       Scenario-selection card uses <label><input type="checkbox"> card-flip
#       pattern; mc_iterations field is a number input with DaisyUI range
#       pairing — neither pattern is supported by form_field's current variants.
#
#   controls/_assignment_row.html
#       HTMX partial: sub-function <select> drives hx-get reload of the row;
#       per-field ranges require inline <input>/<label> pairing not expressible
#       via form_field (see F17 spec-compliance review NIT).
#
#   controls/import.html
#       <input type="file"> is the page's purpose; form_field has no file variant.
#
#   controls/maintenance.html
#       per-row HTMX `hx-target="#assignment-row-{{ a.id }}"` + hx-confirm wiring
#       cannot be expressed via data_table.action_menu (static href/action only).
#
#   help/articles/controls-overlays.html
#   help/articles/methodology-primer.html
#   help/articles/reports.html
#   help/articles/run-and-read-analyses.html
#       Static prose reference tables on the read-only Help article pages
#       (acronym glossary, outputs→action mapping, FAIR-CAM control tables,
#       PDF contents table). Fixed content, not data-driven ORM row sets; the
#       data_table macro (sortable/paginated/projected) is unsuitable for static
#       reference content. Mirrors the former help/index.html precedent (removed
#       from this allowlist after the help rewrite migrated index to card markup).
#
#   layouts/_sidebar.html
#       CSS-only drawer toggle — the <input type="checkbox" class="peer/drawer
#       hidden"> + <label for="..."> pair is a DaisyUI architectural primitive,
#       not a form field; it has no form_field equivalent.
#
#   library/browse.html
#       Debounced HTMX search bar: <input type="search"> with hx-get + delay
#       trigger split across multiple lines; surfaced by F24.a regex refinement
#       (old regex missed multi-line <input\n> tag opens). No form_field
#       search-input variant exists; HTMX attribute density precludes wrapping.
#
#   library/_filter_sidebar.html
#       HTMX filter form: checkbox-per-industry-tier pattern with inline
#       <label><input> pairs; hidden version sentinel. No form_field checkbox
#       variant exists.
#
#   controls/library/browse.html
#       Control library catalog browse (P2b). Mirrors library/browse.html:
#       debounced HTMX <input type="search"> with hx-get + delay trigger. No
#       form_field search-input variant; HTMX attribute density precludes
#       wrapping.
#
#   controls/library/_filter_sidebar.html
#       Control library catalog filter form (P2b). Mirrors
#       library/_filter_sidebar.html: checkbox-per-control-type / FAIR-CAM
#       function inline <label><input> pairs plus NIST/CIS/industry text inputs.
#       No form_field checkbox variant exists.
#
#   library/import.html
#       <input type="file"> is the page's purpose; form_field has no file
#       variant (same constraint as scenarios/import.html, controls/import.html).
#
#   library/import_result.html
#       Row-results summary table after import; same structural constraint as
#       the preview (mirrors scenarios/import_result.html).
#
#   library/overrides/form.html
#       PERT 3-tuple grid (low/mode/high per parameter) — same structural
#       constraint as scenarios/form.html; form_field's max-w-md single-column
#       is too wide for the dense 3-column layout.
#
#   library/overrides/list.html
#       Single raw <table> for the overrides grid; data_table migration is a
#       separate task (F24 scope is cleanup, not migration of all overrides pages).
#
#   register_import/upload.html
#       <input type="file"> is the page's purpose; form_field has no file
#       variant (same constraint as scenarios/import.html, controls/import.html,
#       library/import.html) — epic #34 P1c Task 4.
#
#   organization/form.html
#       Complex org form: custom-styled <select> for industry/size/risk-appetite
#       and a checkbox for cyber-insurance flag — form_field has no checkbox or
#       styled select variant beyond basic text inputs.
#
#   overlays/edit.html
#       Optimistic-locking hidden <input type="hidden" name="expected_version">
#       plus one text input; the hidden field is architectural (not a UI element)
#       and form_field has no hidden-input variant.
#
#   overlays/import_result.html
#       Results summary table after import; same structural constraint as preview.
#
#   overlays/view.html
#       Inline deactivation-reason form: <label><input type="text"> inside a
#       DaisyUI form-control, triggered inline by HTMX — no form_field analogue
#       for inline HTMX action forms.
#
#   runs/components/dist_table.html
#       Loss-distribution ladder (with/without/Δ columns + tail rows); bespoke
#       header + per-row caveat-chip injection not expressible via data_table
#       (redesign P1 T5). Replaces the retired runs/_aggregate_results_panel.html
#       raw table.
#
#   runs/components/control_ledger.html
#       Per-control value ledger (fair-share/range/if-removed/ranking columns
#       with colspan zero-reason rows) + Shapley matrix disclosure; bespoke
#       colspan/badge layout (redesign P1 T6).
#
#   runs/components/controls_snapshot.html
#       Condensed controls snapshot (one row/control, per-assignment detail in a
#       nested <details> xs table); bespoke expansion layout (redesign P1 T6).
#       Replaces the retired runs/detail.html raw controls tables.
#
#   runs/_history_list.html
#       Inline results history table; same structural constraint as aggregate panel.
#
#   runs/_results_panel.html
#       Single-run results table; same structural constraint as aggregate panel.
#
#   scenarios/_attack_mapping_row.html
#       HTMX-free Tier-2 combobox (issue #475): same subFunctionCombobox pattern
#       as controls/_assignment_row.html — hidden native <select> is the form's
#       source of truth, wrapped by a typeahead <label>/<input> combobox pairing
#       not expressible via form_field (see the controls row's F16 header
#       comment for the shared rationale).
#
#   scenarios/form.html
#       inline `pert_input` Jinja macro renders a 3-column dense PERT grid that
#       exceeds form_field's max-w-md single-column constraint.
#
#   scenarios/view.html
#       Controls summary table + optimistic-locking hidden field for confirm
#       action; table has per-row HTMX confirm targets not expressible via
#       data_table.action_menu.
#
#   scenarios/import.html
#       <input type="file"> is the page's purpose; form_field has no file
#       variant (same constraint as controls/import.html).
#
#   scenarios/import_result.html
#       Row-errors summary table after import; same structural constraint as
#       the preview (mirrors overlays/import_result.html).
#
#   scenarios/wizard/step_1_library.html
#       Wizard picker filter bar: debounced HTMX <input type="search"> + two
#       facet <select> dropdowns (asset_class, threat_actor_type) drive
#       hx-get card refresh — same HTMX-density constraint as library/browse.html;
#       no form_field search or facet-select variant.  The radio-card grid
#       was extracted into _step_1_library_cards.html (see below).
#
#   scenarios/wizard/_step_1_library_cards.html
#       Radio-in-label card-select picker: <label class="card"><input type="radio">
#       per library entry. Extracted from step_1_library.html. No form_field
#       radio-card variant exists; mirrors the analyses/new.html card-flip pattern.
#
#   scenarios/wizard/_fair_params_form_inner.html
#       SME-row estimate table is Alpine-driven (`x-for` over `rows`, with
#       `:name="fieldset + '_low_' + idx"` dynamic name bindings). form_field
#       and data_table macros target server-rendered field/table structures
#       with fixed names; they cannot express Alpine-bound dynamic name
#       attributes or template-iterated rows in a `<template x-for>` block.
#       Renamed from _step3_form_inner.html by the 2026-05-28 step-3 split
#       redesign; shared by the Likelihood + Impact page shells (parametrized
#       by fieldsets_on_page) and returned by the HTMX prefill/apply-overlay
#       endpoints as the page-scoped swap fragment.
#
#   scenarios/wizard/step_5_controls.html
#       Debounced Alpine search input + checkbox-per-control; no form_field
#       variant for the debounced x-model pattern or multi-checkbox group.
#       Renamed from step_4_controls.html by the 2026-05-28 step-3 split
#       (controls moved from step 4 to step 5 in the 6-step rail).
#
#   setup/wizard.html
#       Setup wizard renders before any user is authenticated; it cannot use
#       form_field macros that assume the Jinja environment has current_user;
#       the selects are also the only two fields on a zero-auth page.
#
#   users/edit.html
#       Role <select> and is_active checkbox; form_field has no checkbox or
#       styled select variant for these fields.
#
#   users/invite.html
#       Role <select> on invite form; same constraint as users/edit.html.
#
#   account/_totp.html
#       TOTP enrollment code field needs inputmode="numeric" (mobile numeric
#       keypad), autocomplete="one-time-code" (browser/OS OTP autofill), and
#       maxlength="10"; form_field's text variant exposes none of these
#       (Strong Auth P1 Task 6).
#
#   auth/mfa_challenge.html
#       MFA-challenge code field needs inputmode="text", autocomplete=
#       "one-time-code" (browser/OS OTP autofill), and maxlength="32" (TOTP
#       digits or a recovery code); form_field's text variant exposes none of
#       these (Strong Auth P1 Task 8).
#
#   account/security.html
#       Passkey-nickname inline <input>: replaces prompt() (which steals
#       document focus and makes iOS WebKit reject the WebAuthn ceremony
#       with "The document is not focused"); the field pairs with an
#       onclick ceremony button, not a submitted form — no form_field
#       variant for a non-form JS-consumed input.
#
#   auth/step_up.html
#       Step-up challenge (P2 Task 1): same code-field constraint as
#       auth/mfa_challenge.html (inputmode/autocomplete/maxlength) PLUS a
#       password variant for factor-less accounts; form_field's text variant
#       exposes neither.
#
ALLOWLIST: set[str] = {
    "analyses/new.html",
    "controls/_assignment_row.html",
    "controls/import.html",
    "controls/maintenance.html",
    "help/articles/controls-overlays.html",
    "help/articles/methodology-primer.html",
    "help/articles/reports.html",
    "help/articles/run-and-read-analyses.html",
    "layouts/_sidebar.html",
    "library/browse.html",
    "library/_filter_sidebar.html",
    "controls/library/browse.html",
    "controls/library/_filter_sidebar.html",
    "library/import.html",
    "library/import_result.html",
    "library/overrides/form.html",
    "library/overrides/list.html",
    "register_import/upload.html",
    "organization/form.html",
    "overlays/edit.html",
    "overlays/import_result.html",
    "overlays/view.html",
    # #438: bespoke 3-way re-sync diff table (adopted vs control vs entry) —
    # not a data_table shape (per-cell provenance badges, conditional columns).
    "controls/resync.html",
    # #144: NIST-tag input needs hx-get/hx-trigger/hx-target attrs that
    # form_field does not expose.
    "controls/form.html",
    "runs/components/dist_table.html",
    "runs/components/control_ledger.html",
    "runs/components/controls_snapshot.html",
    "runs/_history_list.html",
    "runs/_results_panel.html",
    "scenarios/_attack_mapping_row.html",
    "scenarios/form.html",
    "scenarios/view.html",
    "scenarios/import.html",
    "scenarios/import_result.html",
    "scenarios/wizard/_fair_params_form_inner.html",
    # Milestone B (#loss-pert-overhaul): the step-4 catastrophic toggle needs
    # Alpine x-model + custom value/tooltip that form_field(input_type='toggle')
    # doesn't support (it hardcodes value="true" + On/Off text).
    "scenarios/wizard/step_4_impact.html",
    "scenarios/wizard/_step_1_library_cards.html",
    "scenarios/wizard/step_1_library.html",
    "scenarios/wizard/step_5_controls.html",
    "setup/wizard.html",
    "users/edit.html",
    "users/invite.html",
    "account/_totp.html",
    "auth/mfa_challenge.html",
    "account/security.html",
    "auth/step_up.html",
}


def test_no_raw_markup_outside_macros() -> None:
    offenders: list[tuple[str, int, str]] = []
    for path in TEMPLATES_ROOT.rglob("*.html"):
        rel = path.relative_to(TEMPLATES_ROOT)
        if rel.parts and rel.parts[0] in ALLOWED_DIRS:
            continue
        if str(rel).replace("\\", "/") in ALLOWLIST:
            continue
        for line_no, line in enumerate(path.read_text().splitlines(), start=1):
            if FORBIDDEN.search(line):
                offenders.append((str(rel), line_no, line.strip()))
    assert not offenders, (
        "Raw <table>/<input>/<select>/<label> found outside macros/. "
        "Use the data_table or form_field macros instead, or add to ALLOWLIST "
        "with a one-line justification comment.\n  "
        + "\n  ".join(f"{p}:{ln}: {snippet}" for p, ln, snippet in offenders)
    )


def test_allowlist_does_not_grow_silently() -> None:
    """Plan-gate F24.a (X-F24-4): each allowlist entry should have a justification
    in the source comments. A future PR that adds an entry without a justification
    should fail this test.

    This pins the upper bound. If you NEED to add another entry, you must
    (a) bump this bound, and (b) document the new entry in the comment block above.
    """
    # 39 = 38 + step_4_impact.html (Milestone B #loss-pert-overhaul: the
    # catastrophic toggle needs Alpine x-model/custom value the form_field
    # toggle variant doesn't support — justified inline in ALLOWLIST).
    # 43 = 41 + register_import/upload.html (epic #34 P1c Task 4: file input,
    #      same file-variant gap as its scenarios/controls/library import.html
    #      siblings — justified inline in ALLOWLIST)
    #    + account/_totp.html (Strong Auth P1 Task 6: TOTP code field needs
    #      inputmode/autocomplete/maxlength that form_field's text variant
    #      doesn't expose — justified inline in ALLOWLIST).
    # 44 = 43 + auth/mfa_challenge.html (Strong Auth P1 Task 8: MFA-challenge
    #      code field needs inputmode/autocomplete/maxlength that form_field's
    #      text variant doesn't expose — justified inline in ALLOWLIST).
    # 45 = 44 + account/security.html (iOS passkey-register fix: inline
    #      nickname <input> replacing the focus-stealing prompt() — justified
    #      inline in ALLOWLIST).
    # 46 = 45 + auth/step_up.html (Strong Auth P2 Task 1: step-up challenge
    #      code/password fields need the same attrs form_field's text variant
    #      doesn't expose — justified inline in ALLOWLIST).
    assert len(ALLOWLIST) <= 46, (
        f"Allowlist has grown to {len(ALLOWLIST)} entries. "
        "Each new entry must be justified in the comment block above ALLOWLIST."
    )
