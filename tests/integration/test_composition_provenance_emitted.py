"""#130 Task 8 — composition provenance metadata on the run results payload.

The engine/diagnostic compose FAIR-CAM Boolean groups under per-group rules
(OR / AND / weak-AND). Task 8 emits a structured, code-constant
``composition_provenance`` block into the serialised ``simulation_results`` so
the separate "FAIR-grounding UX" spec can render §/page citations WITHOUT
re-deriving them.

Contracts under test (spec §9 / D6 + plan-gate I-sec-4, N-sec; DSC/kappa
contracts updated Slice 2 Task 5, #439):

  1. The SINGLE-run payload carries a ``composition_provenance`` list, and the
     LEC Response entry reports ``rule == "weak_and"`` with a §3.3 citation.
  2. The list is on the ``_build_results_payload`` ALLOWLIST explicitly (it is
     emitted even though it is not read off ``enhanced`` — it is code-constant).
  3. LEC + VMC + DSC groups are ALL emitted (Slice 2 Task 5 resolves N-M8: the
     DSC groups now ship the Task-1 PROXY-labeled citation strings instead of
     the retracted unverified §5.1 claim), plus one ``reliability_coupling``
     entry for the kappa meta->reliability interpolation.
  4. Provenance values are code-constant only (safe-render invariant): every
     emitted field is sourced from GROUP_TYPE / GROUP_NODE_MAPPING /
     WEAK_AND_OPERATOR_PROVENANCE / KAPPA_META_RELIABILITY — never
     control/scenario/org free-text. The payload is identical regardless of
     the (mocked) ``enhanced`` contents.
  5. The aggregate top-level payload also carries the same block.
  6. Pre-#130 consumers reading ``.get("composition_provenance", [])`` see the
     key present (no KeyError) on a fresh run.
  7. T5 methodology re-review (#439) DSC/coupling string fixes hold end to
     end through the real ``_build_results_payload`` wiring, not just the
     unit-level ``build_composition_provenance``: ``dsc_prevention``'s
     ``rule_citation`` is DSC-specific (T5-Meth-I1, no longer borrows the
     LEC_RESPONSE §3.3.1-3.3.3 citation), and ``reliability_coupling``'s
     ``dsc_note`` distinguishes Standard-prescribed structure from the
     deviating weak-AND operator instead of overclaiming both (T5-Meth-I2).
     These assertions intentionally pin the POST-fix strings.
"""

from __future__ import annotations

from types import SimpleNamespace

from idraa.services.run_executor import (
    _build_aggregate_results_payload,
    _build_results_payload,
)


def _fair_risk(*, ale: float) -> SimpleNamespace:
    return SimpleNamespace(
        loss_event_frequency=0.0,
        loss_magnitude=0.0,
        annualized_loss_expectancy=ale,
        mean=0.0,
        median=0.0,
        mode=0.0,
        std_deviation=0.0,
        var_95=0.0,
        var_99=0.0,
        simulation_results=None,
        n_simulations=10000,
    )


def _enhanced(*, base_ale: float = 100_000.0, residual_ale: float = 50_000.0) -> SimpleNamespace:
    return SimpleNamespace(
        base_risk=_fair_risk(ale=base_ale),
        residual_risk=_fair_risk(ale=residual_ale),
        control_adjustments=[],
        confidence_intervals=SimpleNamespace(
            confidence_level=0.95,
            lower_bound=0.0,
            upper_bound=0.0,
            standard_error=0.0,
            sample_size=10000,
        ),
    )


def _aggregate(*, n: int = 2) -> SimpleNamespace:
    per_scenario = []
    for i in range(n):
        ps = _enhanced()
        ps.scenario_id = f"s-{i}"
        ps.scenario_name = f"Scenario {i}"
        per_scenario.append(ps)
    return SimpleNamespace(
        per_scenario=per_scenario,
        aggregate_with_controls=_fair_risk(ale=100_000.0),
        aggregate_without_controls=_fair_risk(ale=200_000.0),
        confidence_intervals=SimpleNamespace(
            confidence_level=0.95,
            lower_bound=0.0,
            upper_bound=0.0,
            standard_error=0.0,
            sample_size=10000,
        ),
        control_value_dollars=0.0,
        control_value_percent=0.0,
        n_scenarios=n,
        n_simulations=10000,
    )


# --------------------------------------------------------------------------- #
# Contract 1 — Response weak-AND provenance present with §3.3 citation
# --------------------------------------------------------------------------- #


def test_run_results_carry_composition_provenance() -> None:
    payload = _build_results_payload(_enhanced())
    prov = payload["composition_provenance"]
    resp = next(p for p in prov if p["group"] == "lec_response")
    assert resp["rule"] == "weak_and"
    assert "§3.3" in resp["citation"]


# --------------------------------------------------------------------------- #
# Contract 2 — on the allowlist (present even though not read off `enhanced`)
# --------------------------------------------------------------------------- #


def test_composition_provenance_on_allowlist() -> None:
    payload = _build_results_payload(_enhanced())
    assert "composition_provenance" in payload
    assert isinstance(payload["composition_provenance"], list)
    assert len(payload["composition_provenance"]) > 0
    for entry in payload["composition_provenance"]:
        assert set(entry) >= {"group", "rule", "citation", "weights_provenance"}


# --------------------------------------------------------------------------- #
# Contract 3 — LEC + VMC + DSC all emitted; DSC carries the PROXY label
# (Slice 2 Task 5, #439 — resolves N-M8)
# --------------------------------------------------------------------------- #


def test_lec_vmc_dsc_groups_and_coupling_entry_emitted() -> None:
    payload = _build_results_payload(_enhanced())
    groups = {p["group"] for p in payload["composition_provenance"]}
    assert all(
        g.startswith(("lec_", "vmc_", "dsc_")) or g == "reliability_coupling" for g in groups
    ), groups
    # DSC groups ARE now emitted (N-M8 resolved: honest PROXY label replaces
    # the retracted unverified §5.1 claim).
    assert "dsc_prevention" in groups
    assert "dsc_identification_correction_pair" in groups
    # Engine-exercised LEC + VMC groups still present.
    assert "lec_prevention" in groups
    assert "lec_response" in groups
    assert "vmc_variance_prevention" in groups
    # The kappa meta->reliability coupling entry is present.
    assert "reliability_coupling" in groups


def test_dsc_citation_carries_proxy_label_not_retracted_51_claim() -> None:
    """The retracted unverified §5.1 DSC citation string must never appear;
    DSC's emitted citation is the honest Task-1 PROXY-labeled string instead."""
    payload = _build_results_payload(_enhanced())
    by_group = {p["group"]: p for p in payload["composition_provenance"]}
    dsc_prevention_citation = by_group["dsc_prevention"]["citation"]
    dsc_pair_citation = by_group["dsc_identification_correction_pair"]["citation"]
    for citation in (dsc_prevention_citation, dsc_pair_citation):
        assert "§5.1" not in citation
        assert "PROXY" in citation


# --------------------------------------------------------------------------- #
# Contract 4 — code-constant (safe-render): independent of `enhanced` contents
# --------------------------------------------------------------------------- #


def test_provenance_is_code_constant_independent_of_enhanced() -> None:
    a = _build_results_payload(_enhanced(base_ale=1.0, residual_ale=0.5))
    b = _build_results_payload(_enhanced(base_ale=9_999_999.0, residual_ale=42.0))
    assert a["composition_provenance"] == b["composition_provenance"]


def test_weak_and_entries_carry_operator_formula_provenance() -> None:
    """weak-AND groups carry the operator formula provenance (honest-labeling:
    rule Standard-cited, formula implementation-defined).

    #453: DSC_PREVENTION keeps the weak_and FAMILY label but overrides `formula`
    to the best-coherent-subset mean it actually applies (monotone envelope of
    mean-of-present). Only the non-DSC weak-AND groups (e.g. LEC Response) carry
    the shared equal-weighted-mean formula; DSC is asserted separately.
    """
    payload = _build_results_payload(_enhanced())
    weak = [p for p in payload["composition_provenance"] if p["rule"] == "weak_and"]
    assert weak, "expected at least the LEC Response weak-AND group"
    non_dsc = [p for p in weak if p["group"] != "dsc_prevention"]
    assert non_dsc, "expected at least the LEC Response weak-AND group"
    for entry in non_dsc:
        assert entry["formula"] == "weighted arithmetic mean (equal weights)"
        assert "implementation-defined" in entry["formula_provenance"]

    # #453: DSC_PREVENTION's formula is the best-coherent-subset-mean override.
    # F453-Meth-1 (methodology re-review): the formula string discloses the
    # max() equivalence explicitly rather than leaving "best-coherent-subset
    # mean" to imply breadth of present functions raises E.
    dsc = next(p for p in weak if p["group"] == "dsc_prevention")
    assert dsc["formula"] == (
        "E = max_k mean(top-k present member opeffs) — best-coherent-subset "
        "mean (monotone envelope of mean-of-present; #453); equivalently: the "
        "maximum present member opeff — breadth of present functions does "
        "not increase E"
    )
    # F453-Meth-2: formula_provenance points at the actual property pins
    # (test_meta_reliability_coupling.py), not the shared
    # WEAK_AND_OPERATOR_PROVENANCE.formula_provenance (which cites property
    # proofs for the equal-weighted-mean operator, not this one).
    assert dsc["formula_provenance"] == (
        "implementation-defined (#453); properties (monotone in membership, "
        "bounded [0,1], idempotent on homogeneous inputs, empty -> None) "
        "pinned in fair_cam/tests/risk_engine/test_meta_reliability_coupling.py"
    )


# --------------------------------------------------------------------------- #
# Contract 5 — aggregate top-level payload carries the block
# --------------------------------------------------------------------------- #


def test_aggregate_payload_carries_composition_provenance() -> None:
    payload = _build_aggregate_results_payload(_aggregate())
    assert "composition_provenance" in payload
    resp = next(p for p in payload["composition_provenance"] if p["group"] == "lec_response")
    assert resp["rule"] == "weak_and"
    # Per-scenario entries (full SINGLE-shape payloads) also carry it.
    for ps in payload["per_scenario"]:
        assert "composition_provenance" in ps


# --------------------------------------------------------------------------- #
# Contract 6 — pre-#130 consumers get the key (no KeyError)
# --------------------------------------------------------------------------- #


def test_pre_130_get_default_yields_present_key() -> None:
    payload = _build_results_payload(_enhanced())
    assert payload.get("composition_provenance", []) != []


# --------------------------------------------------------------------------- #
# Contract 7 — T5 methodology re-review (#439) DSC/coupling string fixes hold
# end to end through the real payload-building wiring.
# --------------------------------------------------------------------------- #


def test_dsc_prevention_rule_citation_is_dsc_specific_not_shared_lec_response() -> None:
    """T5-Meth-I1: dsc_prevention's rule_citation must not carry the shared
    WEAK_AND_OPERATOR_PROVENANCE.rule_citation (§3.3.1-3.3.3 pp.18-20),
    which is LEC_RESPONSE's citation, not DSC's. It is overridden per-entry
    to point at the deviation note instead. This pins the POST-fix string
    intentionally, per the T5 review rationale."""
    payload = _build_results_payload(_enhanced())
    by_group = {p["group"]: p for p in payload["composition_provenance"]}
    dsc_prev = by_group["dsc_prevention"]
    assert dsc_prev["rule_citation"] == (
        "documented deviation from the nine FAIR-CAM §5.1.x pp.36-45 "
        "Boolean-AND prescriptions; see weak_and_deviation_note"
    )
    assert "§3.3" not in dsc_prev["rule_citation"]

    resp = by_group["lec_response"]
    assert "§3.3" in resp["rule_citation"]


def test_reliability_coupling_dsc_note_distinguishes_structure_from_deviation() -> None:
    """T5-Meth-I2: dsc_note must not claim the DSC group STRUCTURES are
    wholesale Standard-prescribed — DSC_PREVENTION's weak-AND is a documented
    deviation. Pins the POST-fix string intentionally, per the T5 review
    rationale."""
    payload = _build_results_payload(_enhanced())
    by_group = {p["group"]: p for p in payload["composition_provenance"]}
    rc = by_group["reliability_coupling"]
    assert "DSC pair AND and functional decomposition are Standard-prescribed" in rc["dsc_note"]
    assert "documented deviation" in rc["dsc_note"]
    assert '"Operational Performance of other controls"' in rc["citation"]


def test_vmc_identification_or_and_family_fusions_labeled_v3_arithmetic() -> None:
    """Final-Meth-1/Final-Meth-2 (#439/#451 final-gate) hold end to end through
    the real payload-building wiring (not just unit-level
    build_composition_provenance):

    - vmc_identification pairs rule="or" with a §4 p.21 NODE-TARGET citation,
      so it must carry a `rule_provenance` override marking the TI∨monitoring
      OR as a v3 coverage-union choice, not Standard-traced (mirrors the
      dsc_prevention rule_citation override pattern).
    - reliability_coupling's structure_provenance must name the per-family
      E_vmc/E_dsc OR-fusions as v3 arithmetic, not only the top-level E_meta
      fusion. Pins the POST-fix strings intentionally.
    """
    payload = _build_results_payload(_enhanced())
    by_group = {p["group"]: p for p in payload["composition_provenance"]}

    vmc_id = by_group["vmc_identification"]
    assert vmc_id["rule"] == "or"
    assert "v3 arithmetic choice" in vmc_id["rule_provenance"]
    assert "no intra-pair operator prescribed" in vmc_id["rule_provenance"]

    sp = by_group["reliability_coupling"]["structure_provenance"]
    assert "E_vmc = OR(VMC_VARIANCE_PREVENTION" in sp
    assert "E_dsc = OR(DSC_PREVENTION" in sp
    assert "E_meta = OR(E_vmc, E_dsc)" in sp
