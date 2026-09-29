"""Epic F control-library score-delta pins (spec §4.2.5, #192 A5).

Every row composes fair_cam ``Control``s built from the COMMITTED seed entries with
``compose_groups`` at the default κ = 0.5 and pins the group effectiveness before
(entry minus its Epic F assignment) and after (the seed as committed) against the
hand-math column of the plan's Task 6 table.

Hand-math conventions (fair-departures register C3/C6/C11): PROBABILITY part =
capability·coverage·reliability; a null ELAPSED_TIME capability scores
0.5·coverage·reliability; each sub-function is first OR-ed across controls,
1 − Π(1 − x), as if independent; Detection = Visibility·Monitoring·Recognition (AND);
Prevention = OR over its sub-functions; Response = mean of the present members
(weak-AND); the Det∧Resp pair = Detection·Response; DSC Prevention = max of the present
members; E_meta = OR(E_vmc, E_dsc) uplifts every LEC reliability to
r_eff = r0 + (1 − r0)·κ·E_meta.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fair_cam.models.composition_topology import KAPPA_META_RELIABILITY, BooleanGroup
from fair_cam.risk_engine.group_composition import compose_groups

from idraa.services.control_library_scoring import _seed_entry_to_control, classify_entry

SEED_PATH = Path(__file__).parents[2] / "data" / "seed_control_library_entries.json"
REL = 1e-9

# The plan's Task 6 hand-math table is computed at kappa = 0.5 (fair-departures-register
# C7; fair_cam.models.composition_topology.KAPPA_META_RELIABILITY). Passed explicitly to
# every compose_groups call below rather than relying on compose_groups' own default, so a
# future change to that default cannot silently re-pin these values to a different kappa
# without this suite failing loudly (task-6-spec.md NICE 2). test_kappa_matches_the_engine_
# default pins the two constants together so this file's own assumption cannot go stale.
KAPPA = 0.5

SIEM = "security-information-event-management"
FIM = "file-integrity-monitoring"
SSPM = "saas-security-posture-management"
SRA = "secure-remote-access"
SAT = "security-awareness-training"
IR = "incident-response"
EDR = "endpoint-detection-response"
DTE = "data-in-transit-encryption"
SCP = "system-capacity-planning"

# slug -> the assignment Epic F added (removed again to build the "before" entry).
EPIC_F_ADDITIONS: dict[str, str] = {
    SIEM: "lec_det_visibility",
    FIM: "lec_det_visibility",
    SSPM: "lec_det_visibility",
    SRA: "lec_prev_resistance",
    SAT: "dsc_prev_ensure_capability",
}


@pytest.fixture(scope="module")
def seed() -> dict[str, dict[str, Any]]:
    doc = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return {e["slug"]: e for e in doc["entries"]}


def _before(entry: dict[str, Any]) -> dict[str, Any]:
    """The entry with its Epic F assignment stripped (exactly one removed)."""
    sf = EPIC_F_ADDITIONS[entry["slug"]]
    out = copy.deepcopy(entry)
    kept = [a for a in out["assignments"] if a["sub_function"] != sf]
    assert len(kept) == len(out["assignments"]) - 1, f"{entry['slug']} must carry {sf} once"
    out["assignments"] = kept
    return out


def _group(entries: list[dict[str, Any]], group: BooleanGroup) -> float | None:
    result = compose_groups([_seed_entry_to_control(e) for e in entries], kappa=KAPPA)
    return result.group_effectiveness.get(group)


def _pin(entries: list[dict[str, Any]], group: BooleanGroup, expected: float) -> None:
    actual = _group(entries, group)
    assert actual is not None, f"{group.value} composed to None"
    assert actual == pytest.approx(expected, rel=REL)


DET = BooleanGroup.LEC_DETECTION
PAIR = BooleanGroup.LEC_DETECTION_RESPONSE_PAIR
PREV = BooleanGroup.LEC_PREVENTION
DSC = BooleanGroup.DSC_PREVENTION


def test_epic_f_assignments_are_committed_at_the_pinned_values(
    seed: dict[str, dict[str, Any]],
) -> None:
    expected = {
        SIEM: (0.7, 0.8, 0.8),
        FIM: (0.7, 0.8, 0.8),
        SSPM: (0.7, 0.8, 0.8),
        SRA: (0.7, 0.2, 0.8),  # coverage 0.2: gate-2 ruling M2-1
        SAT: (0.7, 0.8, 0.8),
    }
    for slug, sf in EPIC_F_ADDITIONS.items():
        rows = [a for a in seed[slug]["assignments"] if a["sub_function"] == sf]
        assert len(rows) == 1, (slug, sf)
        a = rows[0]
        assert (a["capability_default"], a["coverage_default"], a["reliability_default"]) == (
            expected[slug]
        )
        assert a["capability_provenance"] == "expert-estimate"
        assert a["coverage_provenance"] == "expert-estimate"
        assert a["reliability_provenance"] == "expert-estimate"
        assert len(a["capability_citations"]) == 1


def test_siem_and_fim_visibility_completes_detection_with_ir(
    seed: dict[str, dict[str, Any]],
) -> None:
    # Detection = Vis 0.448 · Mon (null ET: 0.5·0.8·0.8 = 0.32) · Rec 0.448 = 0.06422528;
    # pair = 0.06422528 · IR Event termination 0.32 = 0.0205520896.
    for slug in (SIEM, FIM):
        _pin([_before(seed[slug]), seed[IR]], DET, 0.0)
        _pin([_before(seed[slug]), seed[IR]], PAIR, 0.0)
        _pin([seed[slug], seed[IR]], DET, 0.06422528)
        _pin([seed[slug], seed[IR]], PAIR, 0.0205520896)


def test_sspm_visibility_is_score_neutral_without_a_recognition_carrier(
    seed: dict[str, dict[str, Any]],
) -> None:
    _pin([_before(seed[SSPM])], DET, 0.0)
    _pin([seed[SSPM]], DET, 0.0)
    _pin([_before(seed[SSPM]), seed[IR]], DET, 0.0)
    _pin([seed[SSPM], seed[IR]], DET, 0.0)
    _pin([seed[SSPM], seed[IR]], PAIR, 0.0)


def test_siem_after_plus_sspm_plus_ir(seed: dict[str, dict[str, Any]]) -> None:
    # E_meta = VMC id∧corr = OR(0.448, 0.448) · SSPM implementation (null: 0.32) = 0.22249472;
    # r_eff = 0.8 + 0.2·0.5·0.22249472 = 0.822249472. Adding SSPM's Visibility ORs it with
    # SIEM's: Vis 0.46045970432 -> 1 − (1 − 0.46045970432)².
    before = [seed[SIEM], _before(seed[SSPM]), seed[IR]]
    after = [seed[SIEM], seed[SSPM], seed[IR]]
    result = compose_groups([_seed_entry_to_control(e) for e in after], kappa=KAPPA)
    assert result.meta_strength == pytest.approx(0.22249472, rel=REL)
    # Table values are shown to 10 decimals; the literals below are the exact hand-math to
    # 12 significant digits so the rel=1e-9 pin is not eaten by display rounding.
    _pin(before, DET, 0.116533113311)
    _pin(before, PAIR, 0.0383277163564)
    _pin(after, DET, 0.179407423724)
    _pin(after, PAIR, 0.0590070637720)


def test_edr_plus_siem_plus_ir_detection_rises_55_percent(
    seed: dict[str, dict[str, Any]],
) -> None:
    # Before: Vis 0.448 · OR(0.32, 0.32) = 0.5376 · OR(0.448, 0.448) = 0.695296 -> 0.1674584261.
    # After (C11 ORs SIEM's Visibility with EDR's as if independent; M2-2):
    # 0.695296 · 0.5376 · 0.695296 = 0.2598954772 (+55.2%).
    # Response = mean(EDR Resilience 0.252, IR ET 0.32) = 0.286 (weak-AND).
    before = [seed[EDR], _before(seed[SIEM]), seed[IR]]
    after = [seed[EDR], seed[SIEM], seed[IR]]
    _pin(before, DET, 0.167458426061)
    _pin(before, PAIR, 0.0478931098534)
    _pin(after, DET, 0.259895477246)
    _pin(after, PAIR, 0.0743301064925)
    # N6-4/task-6-spec.md NICE: the ratio comes from the two engine compositions just
    # pinned above, not from re-typing their literals -- a regression that moved BOTH
    # absolute pins by the same factor (so the two `_pin` calls above still passed) would
    # still be caught here.
    before_det = _group(before, DET)
    after_det = _group(after, DET)
    assert before_det is not None
    assert after_det is not None
    assert pytest.approx(1.552, abs=5e-4) == after_det / before_det


def test_sra_resistance_at_coverage_0_2(seed: dict[str, dict[str, Any]]) -> None:
    # Alone: Avoidance 0.448 -> OR(0.448, Resistance 0.7·0.2·0.8 = 0.112) = 0.509824 (+13.8%).
    # With DTE (Resistance 0.448): 1 − 0.552² = 0.695296 -> Resistance OR(0.448, 0.112)
    # = 0.509824; 1 − 0.552·(1 − 0.509824) = 0.729422848 (C11 double credit; M2-1).
    _pin([_before(seed[SRA])], PREV, 0.448)
    _pin([seed[SRA]], PREV, 0.509824)
    _pin([_before(seed[SRA]), seed[DTE]], PREV, 0.695296)
    _pin([seed[SRA], seed[DTE]], PREV, 0.729422848)


def test_sat_ensure_capability_dsc_prevention(seed: dict[str, dict[str, Any]]) -> None:
    # DSC Prevention = max of present members (C6): SAT alone max(0.448, 0.448) = 0.448;
    # with system-capacity-planning the Ensure-capability member ORs to 1 − 0.552² = 0.695296.
    for entries, expected in (
        ([_before(seed[SAT])], 0.448),
        ([seed[SAT]], 0.448),
        ([_before(seed[SAT]), seed[SCP]], 0.448),
        ([seed[SAT], seed[SCP]], 0.695296),
    ):
        _pin(entries, DSC, expected)
        result = compose_groups([_seed_entry_to_control(e) for e in entries], kappa=KAPPA)
        assert result.meta_strength == pytest.approx(expected, rel=REL)


def test_kappa_matches_the_engine_default() -> None:
    # This file's hand-math and every compose_groups(..., kappa=KAPPA) call above assume
    # kappa = 0.5 (fair-departures register C7). Pinning KAPPA to the engine's own default
    # here means a future change to KAPPA_META_RELIABILITY fails loudly in THIS test
    # rather than silently mis-computing every other pin in this module.
    assert pytest.approx(KAPPA_META_RELIABILITY, rel=REL) == KAPPA


def test_entry_level_classification_is_unchanged(seed: dict[str, dict[str, Any]]) -> None:
    expected = {
        SIEM: "non-scoring-residual",
        FIM: "non-scoring-residual",
        SAT: "non-scoring-residual",
        SSPM: "scoring",
        SRA: "scoring",
    }
    for slug, bucket in expected.items():
        assert classify_entry(_before(seed[slug])) == bucket, slug
        assert classify_entry(seed[slug]) == bucket, slug
