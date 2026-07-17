import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from idraa.models.control_library import ControlLibraryEntryAssignment
from idraa.schemas.control_library import ControlLibraryAssignmentSeed, ControlLibraryEntrySeed

_SEED_PATH = Path(__file__).parents[2] / "data" / "seed_control_library_entries.json"
_PILOT_SLUGS = (
    "cloud-security-posture-management",
    "security-awareness-training",
    "data-backup-recovery",
)


def test_assignment_has_per_value_provenance_columns():
    cols = {c.name for c in ControlLibraryEntryAssignment.__table__.columns}
    for v in ("capability", "coverage", "reliability"):
        assert f"{v}_provenance" in cols
        assert f"{v}_citations" in cols


def _kw(**over):
    base = {
        "sub_function": "lec_prev_resistance",
        "coverage_default": 0.8,
        "reliability_default": 0.8,
        "capability_default": 0.7,
    }
    base.update(over)
    return base


def test_cited_value_requires_its_own_citation():
    with pytest.raises(ValidationError):
        ControlLibraryAssignmentSeed(
            **_kw(reliability_provenance="cited", reliability_citations=[])
        )


def test_per_value_independence():  # cited capability + estimated reliability is allowed and recorded distinctly
    a = ControlLibraryAssignmentSeed(
        **_kw(capability_provenance="cited", capability_citations=["MITRE M1037 p.x"])
    )
    assert a.capability_provenance == "cited" and a.reliability_provenance == "expert-estimate"


def test_expert_estimate_ceiling():
    with pytest.raises(ValidationError):  # 0.9 estimate with no citation is blocked
        ControlLibraryAssignmentSeed(
            **_kw(capability_default=0.9, capability_provenance="expert-estimate")
        )


def test_capability_provenance_autofills():  # NEW-B1: un-authored capability is a valid estimate, not an error
    a = ControlLibraryAssignmentSeed(**_kw())  # capability_default=0.7, no provenance authored
    assert a.capability_provenance == "expert-estimate"


def test_natural_unit_capability_exempt_from_ceiling():  # NEW-#1 + IT-1
    # ELAPSED_TIME capability >0.8 with a citation is accepted (ceiling-exempt); expert-estimate is not.
    a = ControlLibraryAssignmentSeed(
        sub_function="vmc_corr_implementation",
        coverage_default=0.8,
        reliability_default=0.8,
        capability_default=3.0,
        capability_provenance="cited",
        capability_citations=[
            "IBM CODB 2024 §3 — median MTTC 3.4 days; 3.0 days chosen as benchmark"
        ],
    )
    assert a.capability_default == 3.0  # accepted despite >0.8 (ceiling-exempt for natural-unit)

    with pytest.raises(
        ValidationError
    ):  # expert-estimate for ELAPSED_TIME manufactures score at lower tail
        ControlLibraryAssignmentSeed(
            sub_function="vmc_corr_implementation",
            coverage_default=0.8,
            reliability_default=0.8,
            capability_default=3.0,
            capability_provenance="expert-estimate",
        )

    # un-authored elapsed_time capability auto-fills to expert-estimate → natural-unit rule fires
    with pytest.raises(ValidationError):
        ControlLibraryAssignmentSeed(
            sub_function="vmc_corr_implementation",
            coverage_default=0.8,
            reliability_default=0.8,
            capability_default=3.0,
        )


def test_currency_capability_requires_citation():  # Finding A — close the currency assign-to-score hole
    with pytest.raises(ValidationError):  # an uncited $ subtractor can manufacture a large score
        ControlLibraryAssignmentSeed(
            sub_function="lec_resp_loss_reduction",
            coverage_default=0.8,
            reliability_default=0.8,
            capability_default=5000.0,
        )
    ok = ControlLibraryAssignmentSeed(
        sub_function="lec_resp_loss_reduction",  # cited is fine
        coverage_default=0.8,
        reliability_default=0.8,
        capability_default=5000.0,
        capability_provenance="cited",
        capability_citations=["IRIS 2024 p.x"],
    )
    assert ok.capability_provenance == "cited"


# ---------------------------------------------------------------------------
# Seed-wide pinning tests (Task 6 — #437)
# ---------------------------------------------------------------------------


def test_every_seed_entry_validates() -> None:
    """All 63 seed entries pass ControlLibraryEntrySeed validation.

    Catches: missing required fields, ceiling violations, orphan provenance,
    duplicate sub_functions, and any other schema-level constraint.
    """
    entries = json.loads(_SEED_PATH.read_text())["entries"]
    assert len(entries) > 0, "seed is empty — unexpected"
    for e in entries:
        ControlLibraryEntrySeed.model_validate(e)


def test_pilot_entries_have_per_value_provenance() -> None:
    """Pilot entries have fully-populated provenance on every authored value.

    The Task-5 methodology review relabeled all MITRE-derived capability values
    from 'cited' to 'expert-estimate', so there are zero 'cited' values in the
    seed.  The test therefore checks that provenance is POPULATED and in the
    valid set {"cited", "expert-estimate"} — not that any specific token appears.

    Checks per assignment:
    - coverage_provenance and reliability_provenance: always present (have defaults).
    - capability_provenance: only checked when capability_default is not None
      (a None capability is a legitimately un-authored slot; its provenance is
      also None by schema invariant).
    """
    by_slug = {e["slug"]: e for e in json.loads(_SEED_PATH.read_text())["entries"]}
    valid_prov = {"cited", "expert-estimate"}
    for slug in _PILOT_SLUGS:
        assert slug in by_slug, f"pilot slug {slug!r} missing from seed"
        entry = ControlLibraryEntrySeed.model_validate(by_slug[slug])
        for a in entry.assignments:
            assert a.coverage_provenance in valid_prov, (
                f"{slug}/{a.sub_function}: coverage_provenance={a.coverage_provenance!r}"
            )
            assert a.reliability_provenance in valid_prov, (
                f"{slug}/{a.sub_function}: reliability_provenance={a.reliability_provenance!r}"
            )
            if a.capability_default is not None:
                assert a.capability_provenance in valid_prov, (
                    f"{slug}/{a.sub_function}: capability_provenance={a.capability_provenance!r}"
                )
