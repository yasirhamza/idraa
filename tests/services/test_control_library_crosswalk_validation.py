"""Gate §7: a ControlLibraryEntry's claimed FAIR-CAM functions (its assignment
sub_functions) must be COVERED by the crosswalk functions of its NIST/CIS tags.
ISO/CSA tags are NOT crosswalk-seeded (gate F-4) and are excluded from validation."""

import pytest

from idraa.models.enums import FairCamSubFunction as F
from idraa.models.framework_crosswalk import FrameworkControl, FrameworkControlFairCam
from idraa.services.control_library_validation import unsupported_claims_for_entry


async def _seed_crosswalk(db, framework, version, code, funcs):
    fc = FrameworkControl(
        framework=framework,
        framework_version=version,
        code=code,
        title="t",
        description=None,
        asset_type=None,
        security_function=None,
        citation={"source": "FAIR Institute"},
    )
    db.add(fc)
    await db.flush()
    for fn in funcs:
        db.add(FrameworkControlFairCam(framework_control_id=fc.id, fair_cam_function=fn))
    await db.flush()


@pytest.mark.asyncio
async def test_grounded_entry_has_no_unsupported_claims(db_session):
    await _seed_crosswalk(db_session, "nist_csf", "1.1", "PR.AC-7", [F.LEC_PREV_RESISTANCE])
    unsupported = await unsupported_claims_for_entry(
        db_session,
        nist_csf_subcategories=["PR.AC-7"],
        cis_safeguards=[],
        claimed=[F.LEC_PREV_RESISTANCE],
    )
    assert unsupported == set()


@pytest.mark.asyncio
async def test_overclaim_is_flagged(db_session):
    await _seed_crosswalk(db_session, "nist_csf", "1.1", "PR.AC-7", [F.LEC_PREV_RESISTANCE])
    unsupported = await unsupported_claims_for_entry(
        db_session,
        nist_csf_subcategories=["PR.AC-7"],
        cis_safeguards=[],
        claimed=[F.LEC_PREV_RESISTANCE, F.LEC_RESP_LOSS_REDUCTION],
    )
    assert unsupported == {F.LEC_RESP_LOSS_REDUCTION}


@pytest.mark.asyncio
async def test_cis_tags_contribute_support(db_session):
    await _seed_crosswalk(db_session, "cis", "8.0", "6.3", [F.LEC_PREV_RESISTANCE])
    unsupported = await unsupported_claims_for_entry(
        db_session,
        nist_csf_subcategories=[],
        cis_safeguards=["6.3"],
        claimed=[F.LEC_PREV_RESISTANCE],
    )
    assert unsupported == set()
