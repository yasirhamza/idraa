"""Canonical τ table — pinning + completeness + sane-bounds tests (PR μ.1).

Pinning is enforced at the SHA-256 digest level. Modifying a value
requires updating BASELINE_DIGEST_2026_05_15 below — that update is a
deliberate methodology change (not a free edit) and must be paired with
a methodology-doc update + backtest re-pin per CLAUDE.md verification
policy.

Issue #131 recalibration (2026-05-16): three KEPT entries with new τ
values + six DROPPED entries reclassified to UnitType.PROBABILITY in
SUB_FUNCTION_UNITS (both the v3 mirror in idraa.models.enums and the
fair_cam mirror imported below). The completeness test
``test_all_elapsed_time_sub_functions_have_tau`` now asserts the post-
issue-#131 invariant: TAU_BY_SUB_FUNCTION keys == ELAPSED_TIME slugs in
SUB_FUNCTION_UNITS (so adding an ELAPSED_TIME sub-function without a τ
entry, or vice versa, is caught at test time). The constant name is
retained (vs renaming to BASELINE_DIGEST_2026_05_16) for historical
continuity — the value below is the post-#131 digest.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from fair_cam.calibration.elapsed_time_taus import (
    TAU_BY_SUB_FUNCTION,
    get_canonical_tau,
)
from fair_cam.models.sub_function import (
    SUB_FUNCTION_UNITS,
    FairCamSubFunction,
    UnitType,
)

BASELINE_DIGEST_2026_05_15 = "47cb3ec2f2af1d3968cdcdf05c075f403d7b9994993db5331db072ae03aa0286"


def test_tau_table_byte_stable() -> None:
    """SHA-256 of sorted(τ table) entries must match baseline."""
    serialized = json.dumps(
        sorted((sf.value, tau) for sf, tau in TAU_BY_SUB_FUNCTION.items()),
        separators=(",", ":"),
    )
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    assert digest == BASELINE_DIGEST_2026_05_15, (
        f"τ table digest drift detected. Old: {BASELINE_DIGEST_2026_05_15}, "
        f"New: {digest}. If intentional, update baseline AND add a methodology-"
        f"doc entry with side-by-side hand-math + actual per CLAUDE.md "
        f"verification policy."
    )


def test_all_elapsed_time_sub_functions_have_tau() -> None:
    """Completeness invariant (issue #131 Arch-B2): every ELAPSED_TIME sub-function
    has a τ entry, and every τ entry maps to an ELAPSED_TIME sub-function.

    Post-issue-#131 the surviving ELAPSED_TIME set is {LEC_DET_MONITORING,
    LEC_RESP_EVENT_TERMINATION, VMC_CORR_IMPLEMENTATION}. Imports
    SUB_FUNCTION_UNITS from ``fair_cam.models.sub_function`` (not
    ``idraa.models.enums``) per the dependency-direction rule —
    fair_cam must not import from idraa. The v3↔fair_cam parity test
    at ``tests/integration/test_fair_cam_v3_unit_type_parity.py``
    enforces the table-level equivalence.
    """
    elapsed_time_sfs = {sf for sf, ut in SUB_FUNCTION_UNITS.items() if ut == UnitType.ELAPSED_TIME}
    assert elapsed_time_sfs == set(TAU_BY_SUB_FUNCTION.keys()), (
        f"Missing τ for: {elapsed_time_sfs - set(TAU_BY_SUB_FUNCTION.keys())}; "
        f"Extra τ for: {set(TAU_BY_SUB_FUNCTION.keys()) - elapsed_time_sfs}"
    )


def test_no_non_elapsed_time_sub_functions() -> None:
    """τ table must NOT contain PROBABILITY / PERCENT_REDUCTION / CURRENCY."""
    for sf in TAU_BY_SUB_FUNCTION:
        assert SUB_FUNCTION_UNITS[sf] == UnitType.ELAPSED_TIME, (
            f"{sf.value} has unit {SUB_FUNCTION_UNITS[sf].value}, "
            f"not ELAPSED_TIME — should not be in τ table"
        )


def test_all_taus_within_sane_bounds() -> None:
    """τ ≥ 1 day, ≤ 3650 days (10 years). Out-of-bounds is suspect."""
    for sf, tau in TAU_BY_SUB_FUNCTION.items():
        assert 1.0 <= tau <= 3650.0, f"{sf.value}: τ={tau} out of sane bounds"


def test_accessor_returns_canonical_value() -> None:
    """get_canonical_tau(sf) returns the same value as the dict."""
    sf = FairCamSubFunction.LEC_DET_MONITORING
    assert get_canonical_tau(sf) == TAU_BY_SUB_FUNCTION[sf]


def test_table_is_immutable() -> None:
    """MappingProxyType prevents runtime mutation (Spec-N2)."""
    with pytest.raises(TypeError):
        TAU_BY_SUB_FUNCTION[FairCamSubFunction.LEC_DET_MONITORING] = 999.0  # type: ignore
