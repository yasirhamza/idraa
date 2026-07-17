import pytest

from idraa.models.enums import FairCamSubFunction as F
from idraa.models.framework_crosswalk import FrameworkControl, FrameworkControlFairCam
from idraa.services.crosswalk import CrosswalkService, MultipleVersionsError


async def _seed(
    db, framework="nist_csf", version="1.1", code="PR.AC-7", func=F.LEC_PREV_RESISTANCE
):
    # ORM-construct (NOT via Alembic — test harness uses create_all). Gate Arch-I2.
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
    db.add(FrameworkControlFairCam(framework_control_id=fc.id, fair_cam_function=func))
    await db.flush()


async def _seed_links(db, framework="nist_csf", version="1.1", *, code, funcs):
    # Seed ONE control (code) carrying >=1 FAIR-CAM links. ORM-construct (gate Arch-I2).
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
    for func in funcs:
        db.add(FrameworkControlFairCam(framework_control_id=fc.id, fair_cam_function=func))
    await db.flush()


@pytest.mark.asyncio
async def test_functions_for_codes_unions(db_session):
    # Genuine union across BOTH dimensions: 3 distinct codes under the same
    # framework+version, collectively carrying 4 DISTINCT functions, with one code
    # (PR.AC-3) carrying 2 links. A regression that returned only the first link
    # (.limit(1) / [0] / dedup-to-one) would drop functions and FAIL the == assertion.
    await _seed_links(
        db_session, code="PR.AC-3", funcs=[F.LEC_PREV_AVOIDANCE, F.LEC_PREV_DETERRENCE]
    )
    await _seed_links(db_session, code="PR.AC-7", funcs=[F.LEC_PREV_RESISTANCE])
    await _seed_links(db_session, code="DE.CM-1", funcs=[F.LEC_DET_VISIBILITY])
    funcs = await CrosswalkService(db_session).faircam_functions_for(
        "nist_csf", ["PR.AC-3", "PR.AC-7", "DE.CM-1"]
    )
    assert funcs == {
        F.LEC_PREV_AVOIDANCE,
        F.LEC_PREV_DETERRENCE,
        F.LEC_PREV_RESISTANCE,
        F.LEC_DET_VISIBILITY,
    }


@pytest.mark.asyncio
async def test_validate_claims_flags_unsupported(db_session):
    await _seed(db_session)
    unsupported = await CrosswalkService(db_session).validate_claims(
        {"nist_csf": ["PR.AC-7"]}, [F.LEC_PREV_RESISTANCE, F.LEC_RESP_LOSS_REDUCTION]
    )
    assert unsupported == {F.LEC_RESP_LOSS_REDUCTION}


@pytest.mark.asyncio
async def test_validate_claims_empty_when_grounded(db_session):
    await _seed(db_session)
    assert (
        await CrosswalkService(db_session).validate_claims(
            {"nist_csf": ["PR.AC-7"]}, [F.LEC_PREV_RESISTANCE]
        )
        == set()
    )


@pytest.mark.asyncio
async def test_multiple_versions_without_explicit_version_raises(db_session):
    # gate M4/Arch-I1: defer-with-guard — two versions coexisting + no explicit
    # version must FAIL LOUDLY rather than silently unioning across versions.
    await _seed(db_session, version="1.1")
    await _seed(db_session, version="2.0", func=F.LEC_RESP_LOSS_REDUCTION)
    with pytest.raises(MultipleVersionsError):
        await CrosswalkService(db_session).faircam_functions_for("nist_csf", ["PR.AC-7"])
    # explicit version disambiguates:
    funcs = await CrosswalkService(db_session).faircam_functions_for(
        "nist_csf", ["PR.AC-7"], framework_version="1.1"
    )
    assert funcs == {F.LEC_PREV_RESISTANCE}
