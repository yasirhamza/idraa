"""Unit tests for control_zero_value_reason / snapshot_sub_functions_by_id / zero_reason
wiring (Issue #436; #439 Slice-2 meta→reliability coupling reconciliation).

Tests cover:
- Meta (VMC id/corr/prev + DSC) → reliability-coupling copy, keyed on has_co_present_lec
  (#439 D1: meta credits ONLY via the κ coupling on a co-present LEC control, so the
  honest reason is "nothing to strengthen" without a partner vs "sub-threshold" with one)
- LEC gaps: resp-without-det, det-without-resp, incomplete/catch-all
- Scorer present → None (real small value, not structural zero)
- Empty sub_functions → None (V1 snapshot, reason unknowable)
- snapshot_sub_functions_by_id: V1 (no assignments), V2/V3 shape
- process_weight_robustness_for_display: zero_reason attached when p50≈0 + snapshot given
  (has_co_present_lec derived from the run's OTHER controls); not attached when p50 is
  non-zero; not attached when sub_functions_by_id is None
"""

from __future__ import annotations

from idraa.services._view_model_helpers import (
    _META_NO_PARTNER_REASON,
    _META_SUBTHRESHOLD_REASON,
    control_zero_value_reason,
    process_weight_robustness_for_display,
    snapshot_sub_functions_by_id,
)

# ---------------------------------------------------------------------------
# control_zero_value_reason: scorer present → None
# ---------------------------------------------------------------------------


def test_reason_lec_prev_scorer_returns_none() -> None:
    """A lec_prev sub-function scores standalone → no structural-zero label."""
    assert control_zero_value_reason(["lec_prev_avoidance"]) is None


def test_reason_lec_prev_deterrence_returns_none() -> None:
    assert control_zero_value_reason(["lec_prev_deterrence"]) is None


def test_reason_vmc_prev_only_classifies_as_meta() -> None:
    """Slice-2 (#439) D1 re-pin: vmc_prev_* no longer scores standalone (Task 1
    retired the VMC direct FAIR-node targets — see test_library_residual). A
    vmc_prev-only control is now META, crediting only via the κ coupling on a
    co-present LEC. With the default has_co_present_lec=True (fail-safe) it maps
    to the sub-threshold copy — NOT None (was a "scorer returns None" pin
    pre-slice), and NOT the Rule-6 "Incomplete" catch-all."""
    reason = control_zero_value_reason(["vmc_prev_reduce_change_freq"])
    assert reason == _META_SUBTHRESHOLD_REASON
    assert "Incomplete" not in reason


def test_reason_currency_subtractor_returns_none() -> None:
    """lec_resp_loss_reduction (currency subtractor) scores standalone → None."""
    assert control_zero_value_reason(["lec_resp_loss_reduction"]) is None


def test_reason_mixed_with_scorer_returns_none() -> None:
    """Any scorer in a mixed set → None regardless of other sub-functions."""
    assert control_zero_value_reason(["vmc_id_control_monitoring", "lec_prev_resistance"]) is None


# ---------------------------------------------------------------------------
# control_zero_value_reason: empty list → None (V1 legacy)
# ---------------------------------------------------------------------------


def test_reason_empty_sub_functions_returns_none() -> None:
    """Empty sub-function list (V1 snapshot) → None (reason unknowable)."""
    assert control_zero_value_reason([]) is None


# ---------------------------------------------------------------------------
# control_zero_value_reason: meta (VMC id/corr/prev + DSC) → reliability-coupling copy
#
# Slice-2 (#439) D1 re-pin: meta controls no longer carry direct FAIR-node targets;
# they credit ONLY via the κ meta→reliability coupling on a co-present LEC control.
# The old "Enabling control / Not modeled / Meta control — not yet modeled" labels are
# obsolete (the value IS modeled via κ now). The classifier returns one of two honest
# strings keyed on has_co_present_lec.
# ---------------------------------------------------------------------------


def test_reason_vmc_id_only_meta_no_partner() -> None:
    """vmc_id_* alone, no co-present LEC → 'nothing to strengthen' copy."""
    reason = control_zero_value_reason(["vmc_id_threat_intelligence"], has_co_present_lec=False)
    assert reason == _META_NO_PARTNER_REASON
    assert "no co-present loss-event control" in reason
    # Obsolete pre-slice wording must be gone.
    assert "not yet modeled" not in reason
    assert "flows through" not in reason


def test_reason_vmc_id_only_meta_with_partner() -> None:
    """vmc_id_* with a co-present LEC (default has_co_present_lec=True) → sub-threshold copy."""
    reason = control_zero_value_reason(["vmc_id_threat_intelligence"])
    assert reason == _META_SUBTHRESHOLD_REASON


def test_reason_vmc_corr_only_meta() -> None:
    """All vmc_corr_* → meta reliability-coupling copy (sub-threshold with default partner)."""
    reason = control_zero_value_reason(["vmc_corr_treatment_selection"])
    assert reason == _META_SUBTHRESHOLD_REASON


def test_reason_vmc_id_and_corr_mixed_meta() -> None:
    """vmc_id + vmc_corr together (still all-meta) → meta copy; no-partner → nothing-to-strengthen."""
    reason = control_zero_value_reason(
        [
            "vmc_id_threat_intelligence",
            "vmc_id_control_monitoring",
            "vmc_corr_treatment_selection",
            "vmc_corr_implementation",
        ],
        has_co_present_lec=False,
    )
    assert reason == _META_NO_PARTNER_REASON


# ---------------------------------------------------------------------------
# control_zero_value_reason: DSC only → meta reliability-coupling copy
# ---------------------------------------------------------------------------


def test_reason_dsc_prev_only_meta() -> None:
    """All dsc_prev_* → meta copy (DSC is meta post-D1). Default partner → sub-threshold."""
    reason = control_zero_value_reason(["dsc_prev_defined_expectations"])
    assert reason == _META_SUBTHRESHOLD_REASON


def test_reason_dsc_id_only_meta_no_partner() -> None:
    """dsc_id_* alone, no partner → 'nothing to strengthen' copy."""
    reason = control_zero_value_reason(["dsc_id_misaligned"], has_co_present_lec=False)
    assert reason == _META_NO_PARTNER_REASON


def test_reason_multiple_dsc_sfs_meta() -> None:
    """Multiple DSC sub-functions → meta reliability-coupling copy."""
    reason = control_zero_value_reason(
        [
            "dsc_prev_communication",
            "dsc_prev_sa_data_asset",
            "dsc_prev_sa_analysis",
        ]
    )
    assert reason == _META_SUBTHRESHOLD_REASON


# ---------------------------------------------------------------------------
# control_zero_value_reason: response without detection partner
# ---------------------------------------------------------------------------


def test_reason_lec_resp_no_det_partner() -> None:
    """lec_resp_event_termination alone (no lec_det_*) → 'No detection partner…'."""
    reason = control_zero_value_reason(["lec_resp_event_termination"])
    assert reason is not None
    assert "No detection partner" in reason
    assert "creditable" in reason


def test_reason_lec_resp_resilience_no_det() -> None:
    """lec_resp_resilience alone → 'No detection partner…'."""
    reason = control_zero_value_reason(["lec_resp_resilience"])
    assert reason is not None
    assert "No detection partner" in reason


def test_reason_lec_resp_with_det_partner_is_incomplete() -> None:
    """lec_resp + lec_det together (both present, neither scores) → catch-all 'Incomplete'.
    No detection-partner label (detection IS present) and no response-partner label
    (response IS present) — falls through to the genuinely-sparse catch-all."""
    reason = control_zero_value_reason(
        [
            "lec_resp_event_termination",
            "lec_det_visibility",
        ]
    )
    # Neither directional label should fire — both sides are present
    assert reason != "No detection partner — response benefit not creditable"
    assert reason != "No response partner — detection benefit not creditable"
    # Falls through to the genuinely-sparse catch-all
    assert reason is not None
    assert "Incomplete" in reason


# ---------------------------------------------------------------------------
# control_zero_value_reason: catch-all / incomplete
# ---------------------------------------------------------------------------


def test_reason_lec_det_only_no_response_partner() -> None:
    """lec_det_* alone (no response partner) → 'No response partner…' (M-4 symmetric label)."""
    reason = control_zero_value_reason(["lec_det_visibility"])
    assert reason is not None
    assert "No response partner" in reason
    assert "creditable" in reason
    # Must NOT mislabel as "Incomplete" — detection-only is a defined structural gap
    assert "Incomplete" not in reason


def test_reason_lec_det_monitoring_no_response_partner() -> None:
    """lec_det_monitoring alone → 'No response partner…' (symmetric to the response-only label)."""
    reason = control_zero_value_reason(["lec_det_monitoring"])
    assert reason is not None
    assert "No response partner" in reason


def test_reason_lec_resp_and_det_both_present_is_incomplete() -> None:
    """lec_det + lec_resp together (both present, neither scores) → catch-all 'Incomplete'.
    This is a genuinely under-authored set: it HAS both sides but neither scores standalone,
    so it's a real authoring gap, not a clean structural reason."""
    reason = control_zero_value_reason(
        [
            "lec_det_visibility",
            "lec_resp_event_termination",
        ]
    )
    assert reason is not None
    assert "Incomplete" in reason


# ---------------------------------------------------------------------------
# snapshot_sub_functions_by_id
# ---------------------------------------------------------------------------


def test_snapshot_sub_functions_v3_shape() -> None:
    """V3 snapshot: assignments list with sub_function dicts → correct extraction."""
    snapshot = [
        {
            "control_id": "aaa",
            "name": "Control A",
            "snapshot_version": 3,
            "assignments": [
                {
                    "sub_function": "lec_prev_avoidance",
                    "capability_value": 0.8,
                    "coverage": 1.0,
                    "reliability": 1.0,
                    "unit_type": "probability",
                },
                {
                    "sub_function": "lec_prev_deterrence",
                    "capability_value": 0.7,
                    "coverage": 1.0,
                    "reliability": 1.0,
                    "unit_type": "probability",
                },
            ],
        },
        {
            "control_id": "bbb",
            "name": "Control B",
            "snapshot_version": 3,
            "assignments": [
                {
                    "sub_function": "vmc_id_control_monitoring",
                    "capability_value": 0.9,
                    "coverage": 1.0,
                    "reliability": 1.0,
                    "unit_type": "probability",
                },
            ],
        },
    ]
    result = snapshot_sub_functions_by_id(snapshot)
    assert result["aaa"] == ["lec_prev_avoidance", "lec_prev_deterrence"]
    assert result["bbb"] == ["vmc_id_control_monitoring"]


def test_snapshot_sub_functions_v1_shape_returns_empty_list() -> None:
    """V1 snapshot (no assignments key) → empty sub-function list for that control."""
    snapshot = [
        {
            "control_id": "ccc",
            "name": "Legacy Control",
            "snapshot_version": 1,
            "control_strength": 0.7,
            "control_reliability": 0.9,
            "control_coverage": 1.0,
            "domain": "LEC",
            "function": "Prevention",
            "type": "Technical",
        }
    ]
    result = snapshot_sub_functions_by_id(snapshot)
    assert result["ccc"] == []


def test_snapshot_sub_functions_empty_snapshot() -> None:
    """Empty snapshot → empty dict."""
    assert snapshot_sub_functions_by_id([]) == {}


def test_snapshot_sub_functions_missing_control_id_skipped() -> None:
    """Entries without control_id are skipped."""
    snapshot = [{"name": "No ID", "assignments": []}]
    result = snapshot_sub_functions_by_id(snapshot)
    assert result == {}


# ---------------------------------------------------------------------------
# process_weight_robustness_for_display: zero_reason wiring
# ---------------------------------------------------------------------------

_IDENTITY = lambda x: x  # noqa: E731  # USD identity convert


def _make_wr(cid: str, p50: float) -> dict:
    """Build a minimal weight_robustness dict with one per-control cell."""
    return {
        "headline": {"reduction_p5": 0.0, "reduction_p50": p50, "reduction_p95": 0.0},
        "per_control": {
            cid: {
                "reduction_p5": 0.0,
                "reduction_p50": p50,
                "reduction_p95": 0.0,
                "rank_p50": 0,
                "rank_min": 0,
                "rank_max": 0,
                "stability_class": "not_applicable",
            }
        },
        "indistinguishable_pairs": [],
        "rank_stability_available": False,
        "draws_used": 0,
        "degraded": False,
        "state": "ok",
    }


def test_zero_reason_attached_when_p50_zero_and_snapshot_given() -> None:
    """$0 cell + snapshot with VMC meta sub-functions, NO other control with an lec_*
    channel → has_co_present_lec derives to False → 'nothing to strengthen' copy."""
    cid = "aaa"
    wr = _make_wr(cid, 0.0)
    sfs = {cid: ["vmc_id_control_monitoring"]}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD", sub_functions_by_id=sfs)
    assert result is not None
    cell = result["per_control"][cid]
    assert cell["zero_reason"] == _META_NO_PARTNER_REASON


def test_zero_reason_meta_with_co_present_lec_control_is_subthreshold() -> None:
    """$0 meta cell + ANOTHER control carrying an lec_* channel → has_co_present_lec
    derives to True from the run's other controls → sub-threshold copy (the meta
    uplift acts on a real partner, it is just below the display threshold)."""
    cid = "meta1"
    lec_cid = "lec1"
    wr = _make_wr(cid, 0.0)
    # Two controls in the run: the $0 meta one + a co-present LEC one.
    sfs = {cid: ["vmc_id_control_monitoring"], lec_cid: ["lec_prev_avoidance"]}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD", sub_functions_by_id=sfs)
    assert result is not None
    cell = result["per_control"][cid]
    assert cell["zero_reason"] == _META_SUBTHRESHOLD_REASON


def test_zero_reason_none_when_p50_nonzero() -> None:
    """Non-zero p50 → zero_reason is None (structural-zero gate not triggered)."""
    cid = "bbb"
    wr = _make_wr(cid, 50_000.0)
    sfs = {cid: ["vmc_id_control_monitoring"]}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD", sub_functions_by_id=sfs)
    assert result is not None
    assert result["per_control"][cid]["zero_reason"] is None


def test_zero_reason_none_when_sub_functions_by_id_not_given() -> None:
    """No sub_functions_by_id passed → zero_reason is None (backward compat)."""
    cid = "ccc"
    wr = _make_wr(cid, 0.0)
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD")
    assert result is not None
    assert result["per_control"][cid]["zero_reason"] is None


def test_zero_reason_none_for_scorer_sub_function() -> None:
    """$0 cell but scorer present → zero_reason is None (genuine overlap-dominated zero)."""
    cid = "ddd"
    wr = _make_wr(cid, 0.0)
    sfs = {cid: ["lec_prev_avoidance"]}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD", sub_functions_by_id=sfs)
    assert result is not None
    assert result["per_control"][cid]["zero_reason"] is None


def test_zero_reason_dsc_label() -> None:
    """$0 cell + DSC sub-functions (sole control, no co-present LEC) → meta
    'nothing to strengthen' copy (DSC is meta post-D1)."""
    cid = "eee"
    wr = _make_wr(cid, 0.0)
    sfs = {cid: ["dsc_prev_communication"]}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD", sub_functions_by_id=sfs)
    assert result is not None
    cell = result["per_control"][cid]
    assert cell["zero_reason"] == _META_NO_PARTNER_REASON


def test_zero_reason_no_detection_partner_label() -> None:
    """$0 cell + lec_resp (no lec_det) → 'No detection partner…'."""
    cid = "fff"
    wr = _make_wr(cid, 0.0)
    sfs = {cid: ["lec_resp_event_termination"]}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD", sub_functions_by_id=sfs)
    assert result is not None
    cell = result["per_control"][cid]
    assert cell["zero_reason"] is not None
    assert "No detection partner" in cell["zero_reason"]


# ---------------------------------------------------------------------------
# M-4 additions: multi-function meta, symmetric detection-only, wiring tests
# ---------------------------------------------------------------------------


def test_reason_vmc_and_dsc_mixed_multi_function_meta() -> None:
    """vmc_id + dsc together (multi-function meta) → meta reliability-coupling copy.
    Must NOT return 'Incomplete' — a control spanning multiple meta categories is
    still pure-meta, not under-authored."""
    reason = control_zero_value_reason(
        [
            "vmc_id_control_monitoring",
            "dsc_prev_defined_expectations",
        ],
        has_co_present_lec=False,
    )
    assert reason == _META_NO_PARTNER_REASON
    # Must NOT mislabel as Incomplete
    assert "Incomplete" not in reason


def test_reason_vmc_corr_and_dsc_multi_function_meta() -> None:
    """vmc_corr + dsc combination → meta copy (sub-threshold with default partner)."""
    reason = control_zero_value_reason(
        [
            "vmc_corr_treatment_selection",
            "vmc_corr_implementation",
            "dsc_id_misaligned",
        ]
    )
    assert reason == _META_SUBTHRESHOLD_REASON
    assert "Incomplete" not in reason


def test_reason_lec_det_single_no_response_partner() -> None:
    """Single lec_det_* assignment (M-4 symmetric Rule 4b) → 'No response partner…'."""
    reason = control_zero_value_reason(["lec_det_visibility"])
    assert reason is not None
    assert "No response partner" in reason
    assert "creditable" in reason


def test_reason_multiple_lec_det_no_response_partner() -> None:
    """Multiple lec_det_* but no lec_resp → 'No response partner…' (same as single)."""
    reason = control_zero_value_reason(["lec_det_visibility", "lec_det_monitoring"])
    assert reason is not None
    assert "No response partner" in reason


def test_zero_reason_meta_control_label() -> None:
    """$0 cell + multi-function meta (vmc + dsc), sole control → meta 'nothing to
    strengthen' copy via wiring path (no co-present LEC)."""
    cid = "ggg"
    wr = _make_wr(cid, 0.0)
    sfs = {cid: ["vmc_id_control_monitoring", "dsc_prev_communication"]}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD", sub_functions_by_id=sfs)
    assert result is not None
    cell = result["per_control"][cid]
    assert cell["zero_reason"] == _META_NO_PARTNER_REASON
    assert "Incomplete" not in cell["zero_reason"]


def test_zero_reason_no_response_partner_label() -> None:
    """$0 cell + lec_det only (no lec_resp) → 'No response partner…' via wiring path."""
    cid = "hhh"
    wr = _make_wr(cid, 0.0)
    sfs = {cid: ["lec_det_visibility"]}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD", sub_functions_by_id=sfs)
    assert result is not None
    cell = result["per_control"][cid]
    assert cell["zero_reason"] is not None
    assert "No response partner" in cell["zero_reason"]


def test_reason_meta_copy_is_truthful_about_coupling() -> None:
    """Slice-2 re-pin: the meta reliability-coupling copy must NOT claim the value is
    'not modeled' (it IS modeled via κ now) and must name the reliability uplift.
    Both cases (partner / no-partner) are exact-pinned to the module constants."""
    with_partner = control_zero_value_reason(["vmc_id_threat_intelligence"])
    assert with_partner == _META_SUBTHRESHOLD_REASON
    assert "reliability uplift" in with_partner.lower()
    assert "not modeled" not in with_partner.lower()

    no_partner = control_zero_value_reason(["vmc_id_threat_intelligence"], has_co_present_lec=False)
    assert no_partner == _META_NO_PARTNER_REASON
    assert "reliability uplift" in no_partner.lower()
    assert "not modeled" not in no_partner.lower()


# ---------------------------------------------------------------------------
# Jinja render smoke test — covers the `elif _cell.zero_reason` template branch
# ---------------------------------------------------------------------------


def test_zero_reason_renders_in_value_range_cell() -> None:
    """Render a minimal inline template that mirrors the `elif _cell.zero_reason` branch
    in _results_panel.html / _aggregate_results_panel.html and assert the reason text
    appears in the HTML output (spec: at least one surface must emit the label)."""
    import jinja2

    # Minimal template that mirrors the `elif _cell.zero_reason` branch in the panel
    # template (range_str absent/dash → show reason label in italic value cell)
    template_src = """\
{% for _cid, _cell in per_control.items() -%}
  {%- if _cell.range_str and _cell.range_str != "—" -%}
    <td class="font-mono">{{ _cell.range_str }}
      {%- if _cell.zero_reason %}<br><span class="italic">{{ _cell.zero_reason }}</span>{%- endif -%}
    </td>
  {%- elif _cell.zero_reason -%}
    <td class="italic">{{ _cell.zero_reason }}</td>
  {%- endif -%}
{%- endfor %}"""

    env = jinja2.Environment(autoescape=True)
    tmpl = env.from_string(template_src)

    zero_reason = _META_SUBTHRESHOLD_REASON
    ctx = {
        "per_control": {
            "ctrl-1": {
                "range_str": "—",  # structural zero: no range to display
                "zero_reason": zero_reason,
                "badge": "stable",
            }
        }
    }
    html = tmpl.render(ctx)
    # The reason label must appear somewhere in the rendered HTML
    assert zero_reason in html, f"zero_reason not found in rendered HTML:\n{html}"
    # The italic class confirms we're in the zero_reason branch, not the range branch
    assert "italic" in html


# ---------------------------------------------------------------------------
# Task 7 (#436): availability_effect param + value-gate regression
# ---------------------------------------------------------------------------


def test_availability_effect_suppresses_no_detection_partner_label():
    # Stealth default: response-only control (no detection) -> structural-$0 label.
    assert "No detection partner" in control_zero_value_reason(["lec_resp_resilience"])
    # Availability effect: the response benefit IS creditable -> no structural
    # reason (a genuine small value, not a structural gap).
    assert control_zero_value_reason(["lec_resp_resilience"], availability_effect=True) is None


def test_availability_effect_does_not_change_detection_only_label():
    # Detection-only gap (Rule 4b) is unrelated to the availability recovery gate.
    assert "No response partner" in control_zero_value_reason(
        ["lec_det_visibility"], availability_effect=True
    )


def test_scored_control_is_value_gated_out_of_zero_reason_regardless_of_flag():
    # reduction_p50 = $5000 (a real score). sub-functions = recovery-only, which
    # WOULD hit Rule 4 at $0. availability_effect=False (the aggregate path default).
    wr = {
        "headline": {"reduction_p5": 100.0, "reduction_p50": 5000.0, "reduction_p95": 9000.0},
        "per_control": {
            "c1": {"reduction_p5": 100.0, "reduction_p50": 5000.0, "reduction_p95": 9000.0},
        },
    }
    out = process_weight_robustness_for_display(
        wr,
        lambda v: v,  # identity convert (USD)
        "USD",
        sub_functions_by_id={"c1": ["lec_resp_resilience"]},
        availability_effect=False,
    )
    # A scored control (p50 >= $1 value-gate) is never sent to the classifier, so it
    # shows NO structural-$0 reason — even though it's a recovery-only control on the
    # aggregate (availability_effect=False) path (the aggregate stale-label guard).
    assert out["per_control"]["c1"]["zero_reason"] is None


# ---------------------------------------------------------------------------
# Task 6 (#439 Slice-2): has_co_present_lec — meta→reliability coupling copy
# ---------------------------------------------------------------------------


def test_meta_control_with_lec_partner_but_subthreshold_gets_truthful_label() -> None:
    """Meta control, has_co_present_lec=True, sub-threshold value → 'below the display
    threshold' copy; NEVER the 'nothing to strengthen' claim (a partner DOES exist)."""
    reason = control_zero_value_reason(["vmc_id_control_monitoring"], has_co_present_lec=True)
    assert reason == _META_SUBTHRESHOLD_REASON
    assert "nothing to strengthen" not in (reason or "")
    assert "below the display threshold" in reason


def test_meta_control_alone_gets_nothing_to_strengthen_label() -> None:
    """Meta control, has_co_present_lec=False → the 'no co-present loss-event control'
    copy (the κ uplift has nothing to act on)."""
    reason = control_zero_value_reason(["vmc_id_control_monitoring"], has_co_present_lec=False)
    assert reason == _META_NO_PARTNER_REASON
    assert "no co-present loss-event control" in reason


def test_vmc_prev_only_control_classifies_as_meta_not_incomplete() -> None:
    """post-D1: vmc_prev_* is meta; it must NOT hit the Rule-6 'Incomplete' catch-all
    regardless of partner presence."""
    for flag in (True, False):
        reason = control_zero_value_reason(["vmc_prev_reduce_change_freq"], has_co_present_lec=flag)
        assert reason is not None
        assert "Incomplete" not in reason


def test_scoring_meta_control_gets_no_label_at_view_model_level() -> None:
    """A meta control whose κ coupling actually scored (p50 >= $1) is value-gated out
    of the classifier at the view-model level → zero_reason is None even though the
    classifier, called directly on the same slugs, would emit a meta label."""
    cid = "meta_scored"
    wr = _make_wr(cid, 250_000.0)
    sfs = {cid: ["vmc_id_control_monitoring"], "lec1": ["lec_prev_avoidance"]}
    result = process_weight_robustness_for_display(wr, _IDENTITY, "USD", sub_functions_by_id=sfs)
    assert result is not None
    assert result["per_control"][cid]["zero_reason"] is None
