"""Caveat registry invariants (spec §Caveat registry).

The registry is the ONLY source of caveat prose for the run-detail views.
Wording of pre-existing route/global strings must be embedded verbatim —
the methodology-approved text is pinned by identity, not paraphrase.
"""

from idraa.services import _view_model_helpers as vmh
from idraa.services.run_caveats import CAVEAT_REGISTRY, active_run_caveats

ALL_FLAGS = {
    "has_cost": True,
    "has_ranges": True,
    "has_zero_reasons": True,
    "has_tail": True,
    "wr_present": True,
    "has_if_removed": True,
    "has_attribution": True,
}


def test_registry_keys_are_the_spec_set_in_summary_render_order():
    # Order IS the chip numbering order (spec: numbering reads monotonically
    # top-to-bottom in the Summary view; plan-gate Arch-N4/Spec-N3).
    assert list(CAVEAT_REGISTRY.keys()) == [
        "mean-vs-typical",  # verdict strip
        "weight-provenance",  # verdict strip (typical-case range)
        "cost-dedup",  # verdict strip (ROI cell)
        "dist-note",  # dist table header
        "independence",  # dist table tail rows
        "mc-interval",  # dist table tail rows (ES 95% MC interval; #508)
        "fair-share",  # control ledger intro
        "if-removed-partial",  # control ledger if-removed column
        "structural-zeros",  # control ledger reason-label rows
    ]


def test_mc_interval_caveat_present_with_tail_and_honest():
    # #508: with tail metrics, the MC-interval caveat appears and its prose is
    # methodology-honest — sampling/convergence error, NOT loss uncertainty.
    result = active_run_caveats(**ALL_FLAGS)
    entry = next((e for e in result["entries"] if e["key"] == "mc-interval"), None)
    assert entry is not None
    body = entry["body"].lower()
    assert "sampling error" in body
    assert "not uncertainty in" in body and "range of possible losses" in body
    assert "seed" in body  # frames it as re-run-under-a-different-seed variability


def test_full_flags_number_all_entries_sequentially():
    result = active_run_caveats(**ALL_FLAGS)
    numbers = [e["number"] for e in result["entries"]]
    assert numbers == list(range(1, len(result["entries"]) + 1))
    assert result["numbers"] == {e["key"]: e["number"] for e in result["entries"]}


def test_flags_gate_conditional_entries():
    result = active_run_caveats(
        has_cost=False,
        has_ranges=False,
        has_zero_reasons=False,
        has_tail=False,
        wr_present=False,
        has_if_removed=False,
        has_attribution=False,
    )
    keys = {e["key"] for e in result["entries"]}
    assert "cost-dedup" not in keys
    assert "if-removed-partial" not in keys
    assert "structural-zeros" not in keys
    assert "independence" not in keys  # tail rows absent -> no tail caveat
    assert "mc-interval" not in keys  # tail rows absent -> no MC-interval caveat (#508)
    # No attribution on the page -> no attribution caveats (Meth-I6/Arch-I5):
    assert "fair-share" not in keys
    assert "weight-provenance" not in keys
    # Always-on entries survive:
    assert "mean-vs-typical" in keys
    assert "dist-note" in keys
    # Numbering re-compacts (no gaps) after gating:
    assert [e["number"] for e in result["entries"]] == list(range(1, len(result["entries"]) + 1))


def test_weight_provenance_survives_ranges_without_attribution():
    # A run can carry a typical-case headline range while the matrix is
    # unavailable — the verdict strip still chips weight-provenance.
    result = active_run_caveats(
        **{
            **ALL_FLAGS,
            "has_attribution": False,
            "has_if_removed": False,
            "has_zero_reasons": False,
        }
    )
    assert "weight-provenance" in {e["key"] for e in result["entries"]}


def test_verbatim_strings_embedded_not_paraphrased():
    body = {e["key"]: e["body"] for e in active_run_caveats(**ALL_FLAGS)["entries"]}
    # With the if-removed column present, weight-provenance MUST embed the
    # _WITH_IF_REMOVED variant (Meth-B2: the 2026-07-03 adjudication legend).
    assert vmh.CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_WITH_IF_REMOVED in body["weight-provenance"]
    assert vmh.IF_REMOVED_LEGEND in body["weight-provenance"]
    # No duplicated too-close-to-call sentence (Meth-N1/Spec-I2):
    assert body["weight-provenance"].count("too close to call") == 1
    # dist-note embeds the definitional note verbatim.
    assert vmh.DIST_STATS_DEFINITIONAL_NOTE in body["dist-note"]


def test_wr_present_without_if_removed_uses_plain_disclaimer():
    result = active_run_caveats(**{**ALL_FLAGS, "has_if_removed": False})
    body = {e["key"]: e["body"] for e in result["entries"]}
    assert vmh.CONTROL_WEIGHT_PROVENANCE_DISCLAIMER in body["weight-provenance"]
    assert vmh.IF_REMOVED_LEGEND not in body["weight-provenance"]


def test_wr_absent_uses_base_disclaimer():
    result = active_run_caveats(
        **{
            **ALL_FLAGS,
            "wr_present": False,
            "has_ranges": False,
            "has_if_removed": False,
            "has_zero_reasons": False,
        }
    )
    body = {e["key"]: e["body"] for e in result["entries"]}
    assert vmh.CONTROL_WEIGHT_PROVENANCE_DISCLAIMER_BASE in body["weight-provenance"]


# ---- Final display slice (2026-07-04): basis-aware mean-vs-typical / fair-share ----


def test_default_basis_is_typical_and_byte_identical_to_legacy_wording():
    """No caller passed basis= before this slice; the default MUST reproduce
    today's exact prose (regression pin against the literal strings this
    module used to hardcode statically, even though the registry entry is now
    built dynamically per-call)."""
    result = active_run_caveats(**ALL_FLAGS)
    body = {e["key"]: e["body"] for e in result["entries"]}
    assert body["mean-vs-typical"] == (
        "Typical-case figures use the median of the loss distribution, which "
        "sits below the average (mean) because a few rare, very large losses "
        "pull the average up. A wider gap means a more skewed loss profile, "
        "not a model error."
    )
    assert body["fair-share"] == (
        "Attribution values are each control's fair share (Shapley) of the "
        "scenario's modeled risk reduction; they sum to the scenario total and "
        "to each control's column total. These are typical-case estimates, "
        "which for skewed losses run below the average (mean) control value "
        "shown in the summary above. Cells shown as — had no attribution "
        "computed (legacy or skipped scenarios) and are excluded from totals — "
        "a row/column total over — cells is a partial sum over the attributed "
        "controls only, not the scenario's full modeled reduction."
    )
    assert vmh.MEAN_BASIS_PAIRING_NOTE not in body["mean-vs-typical"]


def test_explicit_basis_typical_matches_default():
    """basis="typical" passed explicitly must render byte-identical to the
    default (legacy runs that thread their own "typical" basis explicitly
    must not diverge from callers that omit the kwarg)."""
    default_result = active_run_caveats(**ALL_FLAGS)
    explicit_result = active_run_caveats(**ALL_FLAGS, basis="typical")
    assert default_result == explicit_result


def test_mean_basis_rewrites_mean_vs_typical_and_fair_share_honestly():
    """basis="mean": the fair-share/attribution figures are now on the same
    average basis as the headline — the caveat prose must say so, and must
    embed MEAN_BASIS_PAIRING_NOTE by identity (never paraphrased, matching this
    module's "embed by reference" convention for adjudicated strings)."""
    result = active_run_caveats(**ALL_FLAGS, basis="mean")
    body = {e["key"]: e["body"] for e in result["entries"]}
    assert vmh.MEAN_BASIS_PAIRING_NOTE in body["mean-vs-typical"]
    assert "directly comparable" in body["fair-share"]
    assert "typical-case estimates, which for skewed losses run below" not in body["fair-share"]
    # The operational tail (— cells / partial-sum wording) is basis-independent
    # and must survive unchanged in the mean variant too.
    assert "excluded from totals" in body["fair-share"]


def test_mean_basis_does_not_affect_other_caveat_bodies():
    """Only "mean-vs-typical" and "fair-share" are basis-switched; every other
    caveat (weight-provenance, cost-dedup, dist-note, independence,
    if-removed-partial, structural-zeros) renders identically regardless of
    basis."""
    typical_result = active_run_caveats(**ALL_FLAGS, basis="typical")
    mean_result = active_run_caveats(**ALL_FLAGS, basis="mean")
    typical_body = {e["key"]: e["body"] for e in typical_result["entries"]}
    mean_body = {e["key"]: e["body"] for e in mean_result["entries"]}
    for key in typical_body:
        if key in ("mean-vs-typical", "fair-share"):
            continue
        assert typical_body[key] == mean_body[key], f"{key} unexpectedly basis-sensitive"
