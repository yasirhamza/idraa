"""Stale-copy tripwire banner + library loss refresh (sigma-recal PR3 Task
4, D23).

Per docs/superpowers/plans/2026-07-30-sigma-recal-pr3.md Task 4 Step 1.

Fixtures MUST mirror prod shapes (feedback_fixtures_must_mirror_prod_shapes):
this module builds distribution dicts directly via ``seed_scenario_factory``
(rather than routing everything through the wizard/expert-form HTTP paths)
so every case can pin an EXACT stored sigma/provenance shape, including
prod-observed oddities -- a ``secondary_loss`` column holding the literal
4-char JSON text ``"null"`` (not SQL NULL), and a ``vulnerability`` dict
with no ``"distribution"`` key at all (case 1's fixture folds both
prod-shape requirements in, since ``_loss_stale_wide`` never reads
vulnerability and the null-text SL must not crash the banner walk).
"""

from __future__ import annotations

import math
import uuid
from typing import Any

import pytest
from fair_cam.quantile_pooling import Z_0_95
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.models.audit_log import AuditLog
from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.models.scenario_library import ScenarioLibraryEntry
from idraa.routes.scenarios import _field_has_provenance, _stored_loss_sigma
from idraa.services.library_calibration import library_calibrated_pre_fill
from idraa.services.loss_capacity import capacity_max_for_org
from tests.conftest import csrf_post
from tests.integration.test_loss_pinning import (
    _assert_rendered_html_error,
    _create_lognormal_scenario,
    _get_scenario,
    _set_annual_revenue,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _lognormal(
    mean_dollars: float,
    sigma: float,
    *,
    cap: float | None = None,
    stamp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A stored lognormal PL/SL dict with an EXPLICIT sigma -- no p50/p95
    fit needed, since every test in this module wants a precise, hand-known
    sigma rather than one recovered by round-tripping through a quantile
    fit."""
    dist: dict[str, Any] = {
        "distribution": "lognormal",
        "mean": math.log(mean_dollars),
        "sigma": sigma,
    }
    if cap is not None:
        dist["max"] = cap
    if stamp is not None:
        dist["distribution_fit_metadata"] = {"sigma_recalibration": stamp}
    return dist


_ANALYST_PIN_STAMP: dict[str, Any] = {
    "source": "analyst_pin",
    "pinned_at": "2026-07-01T00:00:00+00:00",
    "actor_id": "11111111-1111-1111-1111-111111111111",
    "input": {"p50": 1_000_000.0, "p95": 8_000_000.0},
    "prior_sigma": None,
    "prior_source": None,
}

# A migration_recalibration stamp co-existing with a WIDE stored sigma is an
# artificial combination (the real sweep always narrows to exactly the
# default) -- built here purely to prove _field_has_provenance suppresses on
# the STAMP alone, independent of the numeric width it happens to sit beside
# (plan case 3: "migration stamp on the wide field -> absent").
_MIGRATION_STAMP: dict[str, Any] = {
    "source": "migration_recalibration",
    "swept_at": "2026-06-01T00:00:00+00:00",
    "prior_sigma": 3.1,
}


async def _seed_published_entry(
    db: AsyncSession,
    *,
    slug: str,
    primary_loss: dict[str, Any],
    secondary_loss: dict[str, Any] | None = None,
    version: int = 1,
    entry_id: uuid.UUID | None = None,
    name: str | None = None,
) -> ScenarioLibraryEntry:
    entry = ScenarioLibraryEntry(
        id=entry_id if entry_id is not None else uuid.uuid4(),
        version=version,
        slug=slug,
        name=name or slug,
        status="published",
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="fixture entry",
        canonical_fair_gap="fixture; not a real gap",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2.0},
        vulnerability={"distribution": "PERT", "low": 0.2, "mode": 0.4, "high": 0.6},
        primary_loss=primary_loss,
        secondary_loss=secondary_loss,
        suggested_control_ids=[],
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


def _pin_for(entry: ScenarioLibraryEntry) -> dict[str, Any]:
    return {
        "entry_id": str(entry.id),
        "version": entry.version,
        "override_id": None,
        "override_version": None,
    }


async def _seed_scenario_with_pl(
    factory: Any,
    db: AsyncSession,
    *,
    name: str,
    organization_id: uuid.UUID | None = None,
    primary_loss: dict[str, Any],
    secondary_loss: dict[str, Any] | None = None,
    vulnerability: dict[str, Any] | None = None,
    library_pin: dict[str, Any] | None = None,
) -> Scenario:
    """``seed_scenario_factory``'s own ``primary_loss``/``vulnerability``
    kwargs collide with its hardcoded PERT defaults (``TypeError: multiple
    values``, the same foot-gun test_loss_pinning.py's
    ``test_pin_wrong_org_returns_404`` already documents) -- build with the
    factory's defaults, then mutate the fields THIS module actually needs
    control over on the same session, mirroring that precedent.
    ``organization_id`` omitted (``None``) defers to the factory's own
    default (``seed_organization`` -- the deliberate "third, distinct org"
    shape the wrong-org RBAC case needs)."""
    kwargs: dict[str, Any] = {"name": name}
    if organization_id is not None:
        kwargs["organization_id"] = organization_id
    if secondary_loss is not None:
        kwargs["secondary_loss"] = secondary_loss
    if library_pin is not None:
        kwargs["library_pin"] = library_pin
    scenario = await factory(**kwargs)
    scenario.primary_loss = primary_loss
    if vulnerability is not None:
        scenario.vulnerability = vulnerability
    db.add(scenario)
    await db.commit()
    await db.refresh(scenario)
    return scenario


# ---------------------------------------------------------------------------
# Case 1: banner fires, shows sigma to 2dp, Refresh button present when
# library-linked. Folds in BOTH prod-shape fixture requirements: a
# secondary_loss column holding the literal JSON text "null" (not SQL NULL)
# and a vulnerability dict with no "distribution" key.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_banner_fires_with_refresh_button_when_library_linked(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    entry = await _seed_published_entry(
        db_session,
        slug="banner-fires-entry",
        primary_loss={"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
    )
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Banner-fires",
        organization_id=org_id,
        primary_loss=_lognormal(1_000_000.0, 2.5),
        # Prod shape: vuln dict with no "distribution" key at all.
        vulnerability={"low": 0.2, "mode": 0.4, "high": 0.6},
        library_pin=_pin_for(entry),
    )
    # Prod shape: secondary_loss stored as the literal 4-char JSON text
    # "null" (10 real prod rows have this shape per the migration test
    # suite), not SQL NULL -- must read back as None and never crash the
    # per-field walk.
    await db_session.execute(
        text("UPDATE scenarios SET secondary_loss = 'null' WHERE id = :id"),
        {"id": str(scenario.id)},
    )
    await db_session.commit()

    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert 'data-testid="loss-stale-wide"' in resp.text
    assert "2.50" in resp.text
    assert 'data-testid="loss-refresh-button"' in resp.text


# ---------------------------------------------------------------------------
# Case 2: tolerance boundary. Prod stores 1.7 +/- ~1.5e-7 from dollar
# round-trips; the EXACT observed prod value must NOT fire the banner.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_banner_absent_at_prod_tolerance_boundary(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Prod-boundary-sigma",
        organization_id=org_id,
        # The exact prod-observed sigma (1.7 +/- ~1.4613e-7) -- inside the
        # 1e-5 tolerance, must read as "at the default", not wide.
        primary_loss=_lognormal(1_000_000.0, 1.7000001461320862),
    )
    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert 'data-testid="loss-stale-wide"' not in resp.text


# ---------------------------------------------------------------------------
# Case 3: per-field suppression (SC-1/B-M3a) -- a pin or migration stamp on
# ONE field must never mute a wide, unstamped sibling.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_banner_suppressed_when_the_only_wide_field_is_pinned(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Suppressed-pinned-wide",
        organization_id=org_id,
        primary_loss=_lognormal(1_000_000.0, 2.5, stamp=_ANALYST_PIN_STAMP),
    )
    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert 'data-testid="loss-stale-wide"' not in resp.text


@pytest.mark.asyncio
async def test_banner_suppressed_when_the_only_wide_field_carries_migration_stamp(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Suppressed-migration-wide",
        organization_id=org_id,
        primary_loss=_lognormal(1_000_000.0, 2.5, stamp=_MIGRATION_STAMP),
    )
    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert 'data-testid="loss-stale-wide"' not in resp.text


@pytest.mark.asyncio
async def test_banner_fires_mixed_provenance_migration_pl_beside_wide_unstamped_sl(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    """The discriminating mixed case a SCENARIO-level suppression rule would
    silence: PL carries a (narrow) migration stamp, SL is wide and
    unstamped -- the banner must still fire, attributable to SL."""
    client, org_id = authed_analyst
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Mixed-migration-pl-wide-sl",
        organization_id=org_id,
        primary_loss=_lognormal(1_000_000.0, 1.7, stamp=_MIGRATION_STAMP),
        secondary_loss=_lognormal(200_000.0, 3.0),
    )
    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert 'data-testid="loss-stale-wide"' in resp.text


@pytest.mark.asyncio
async def test_banner_fires_mixed_provenance_pinned_pl_beside_wide_unstamped_sl(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Same mixed-provenance shape as above, but via a REAL pin_loss round
    trip (end-to-end wiring check, not just the dict-shape predicate) --
    pin primary, leave secondary as a wide unstamped lognormal, the banner
    must still fire."""
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(
        client,
        name="Mixed-pinned-pl-wide-sl",
        sl_dist="lognormal",
        sl_low="100000",
        sl_high="200000000",  # implied sigma ~= ln(2000)/(2*Z_0.95) ~= 2.31, wide
    )
    scenario = await _get_scenario(db_session, org_id, "Mixed-pinned-pl-wide-sl")
    sl_sigma = _stored_loss_sigma(scenario.secondary_loss)
    assert sl_sigma is not None and sl_sigma > 1.7 + 1e-5, sl_sigma

    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/pin",
        {
            "field": "primary",
            "pin_p50": "1000000",
            "pin_p95": "8000000",
            "expected_row_version": str(scenario.row_version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text

    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert 'data-testid="loss-stale-wide"' in resp.text


@pytest.mark.asyncio
async def test_banner_present_without_refresh_button_when_no_library_pin(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    """D23: library linkage is NOT required to fire the banner -- wild
    imports / hand-authored wide sigma light it up too. Linkage only gates
    the Refresh affordance; the pin-as-acknowledgment copy shows instead."""
    client, org_id = authed_analyst
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Wide-no-linkage",
        organization_id=org_id,
        primary_loss=_lognormal(1_000_000.0, 2.5),
        # library_pin omitted -> None (the factory/column default).
    )
    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert 'data-testid="loss-stale-wide"' in resp.text
    assert 'data-testid="loss-refresh-button"' not in resp.text
    assert "no linked library entry" in resp.text.lower()


# ---------------------------------------------------------------------------
# Case 3b: mixture read upgrade (B-M3b) -- TRUE mixture implied sigma, not
# max-component. Side-by-side hand math + executed value first (issue #90
# discipline), then the HTTP-level tripwire assertion.
# ---------------------------------------------------------------------------

# Hand math: mu1 = ln(1_000_000), mu2 = mu1 + ln(200) (medians 200x apart),
# both components sigma=1.7, weights [0.5, 0.5]. Executed via
#   python3 -c "
#     import math
#     from fair_cam.quantile_pooling import mixture_quantile_lognorm, Z_0_95
#     from fair_cam.quantile_pooling._types import LognormMixture, LogNormalTruncFit
#     mu1 = math.log(1_000_000.0); mu2 = mu1 + math.log(200.0)
#     c1 = LogNormalTruncFit(mu1, 1.7, 0.0, math.inf)
#     c2 = LogNormalTruncFit(mu2, 1.7, 0.0, math.inf)
#     mix = LognormMixture((c1, c2), (0.5, 0.5))
#     q50, q95 = mixture_quantile_lognorm(mix, 0.5), mixture_quantile_lognorm(mix, 0.95)
#     print(repr(math.log(q95/q50)/Z_0_95))
#   "
# -> 2.935123905982995 (matches the plan's "round-2 executed 2.935124").
_DIVERGENT_MIXTURE_IMPLIED_SIGMA = 2.935123905982995


def _divergent_mixture() -> dict[str, Any]:
    mu1 = math.log(1_000_000.0)
    mu2 = mu1 + math.log(200.0)
    return {
        "distribution": "lognormal_mixture",
        "components": [
            {"mean": mu1, "sigma": 1.7, "weight": 0.5},
            {"mean": mu2, "sigma": 1.7, "weight": 0.5},
        ],
    }


def test_stored_loss_sigma_mixture_reads_true_implied_sigma_not_max_component() -> None:
    read = _stored_loss_sigma(_divergent_mixture())
    assert read is not None
    # Side-by-side: max-component read (the pre-T4 behavior) would have
    # been exactly 1.7 -- the whole point of the upgrade is that a
    # divergent mixture implies FAR more dispersion than either component
    # alone.
    assert read == pytest.approx(_DIVERGENT_MIXTURE_IMPLIED_SIGMA, rel=1e-9)
    assert read > 1.7 + 1e-5


def test_stored_loss_sigma_single_component_mixture_regression() -> None:
    """A single-component mixture reads its component sigma unchanged
    (~1e-16 relative float-association drift from routing through
    mixture_quantile_lognorm's single-component shortcut, not a behavior
    change)."""
    dist = {
        "distribution": "lognormal_mixture",
        "components": [{"mean": math.log(250_000.0), "sigma": 1.9, "weight": 1.0}],
    }
    read = _stored_loss_sigma(dist)
    assert read == pytest.approx(1.9, rel=1e-9)


@pytest.mark.asyncio
async def test_banner_fires_for_divergent_mixture_via_http(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Divergent-mixture-banner",
        organization_id=org_id,
        primary_loss=_divergent_mixture(),
    )
    resp = await client.get(f"/scenarios/{scenario.id}")
    assert resp.status_code == 200
    assert 'data-testid="loss-stale-wide"' in resp.text
    assert "2.94" in resp.text  # 2dp of the executed implied sigma


# ---------------------------------------------------------------------------
# Case 4: refresh two-step -- preview (no mutation) then confirmed write.
# The entry is re-published at v2 with a DIFFERENT primary_loss so the
# assertion that refresh adopts the entry's CURRENT (not the originally-
# linked) version is genuinely discriminating.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_two_step_confirm_then_write_replaces_pl_sl_and_audits(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")

    entry_v1 = await _seed_published_entry(
        db_session,
        slug="refresh-entry",
        primary_loss={"distribution": "lognormal", "mean": math.log(2_000_000.0), "sigma": 0.9},
        secondary_loss={
            "distribution": "PERT",
            "low": 50_000.0,
            "mode": 100_000.0,
            "high": 300_000.0,
        },
    )
    entry_v2 = await _seed_published_entry(
        db_session,
        slug="refresh-entry",
        entry_id=entry_v1.id,
        version=2,
        primary_loss={"distribution": "lognormal", "mean": math.log(1_500_000.0), "sigma": 0.6},
        secondary_loss={
            "distribution": "PERT",
            "low": 40_000.0,
            "mode": 90_000.0,
            "high": 250_000.0,
        },
    )

    org = await db_session.get(Organization, org_id)
    assert org is not None
    expected_max = capacity_max_for_org(org.annual_revenue, get_settings().capacity_k)
    assert expected_max is not None

    # Link the scenario at v1 -- refresh must adopt v2, the CURRENT
    # published version, not the originally-linked one.
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Refresh-happy-path",
        organization_id=org_id,
        primary_loss=_lognormal(500_000.0, 2.8),
        library_pin=_pin_for(entry_v1),
    )
    prior_pl = dict(scenario.primary_loss)
    prior_sl = scenario.secondary_loss
    prior_row_version = scenario.row_version

    # First POST (no confirm): read-only preview, no mutation.
    r1 = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/refresh",
        {"expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    assert r1.status_code == 200, r1.text
    assert 'data-testid="loss-refresh-confirm"' in r1.text
    assert entry_v2.name in r1.text

    await db_session.refresh(scenario)
    assert scenario.primary_loss == prior_pl, "preview step must not mutate"

    # Second POST (confirmed): the real write.
    r2 = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/refresh",
        {"confirm_refresh": "1", "expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    assert r2.status_code == 303, r2.text
    assert r2.headers["location"] == f"/scenarios/{scenario.id}?loss_refreshed=1"

    # A fresh select() would return the SAME already-identity-mapped `scenario`
    # object with its now-STALE in-memory attributes (SQLAlchemy's default
    # identity-map-wins merge policy) -- the app's own request-scoped session
    # committed the write, but this session's copy of the object was never
    # told to re-fetch. db_session.refresh() forces that re-fetch (the same
    # idiom test_pin_clears_on_edit_save_and_banner_refires already uses).
    await db_session.refresh(scenario)
    refreshed = scenario

    form_dict, _meta = library_calibrated_pre_fill(entry_v2, None)
    expected_pl = dict(form_dict["pl"])
    expected_pl["max"] = expected_max
    expected_sl = dict(form_dict["sl"]) if form_dict["sl"] is not None else None

    assert refreshed.primary_loss == expected_pl
    assert refreshed.secondary_loss == expected_sl
    assert refreshed.library_pin is not None
    assert refreshed.library_pin["entry_id"] == str(entry_v2.id)
    assert refreshed.library_pin["version"] == entry_v2.version == 2
    assert refreshed.row_version == prior_row_version + 1

    audit_row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_id == scenario.id,
                AuditLog.action == "scenario.loss_refreshed_from_library",
            )
        )
    ).scalar_one()
    assert audit_row.changes["primary_loss"] == [prior_pl, expected_pl]
    assert audit_row.changes["secondary_loss"] == [prior_sl, expected_sl]
    assert audit_row.changes["library_pin"][1]["version"] == 2


# ---------------------------------------------------------------------------
# Case 5: null-SL entry shape -- secondary_loss becomes None, not {}.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_null_sl_entry_clears_secondary_loss(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    entry = await _seed_published_entry(
        db_session,
        slug="refresh-null-sl-entry",
        primary_loss={"distribution": "lognormal", "mean": math.log(2_000_000.0), "sigma": 0.9},
        secondary_loss=None,
    )
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Refresh-null-sl",
        organization_id=org_id,
        primary_loss=_lognormal(500_000.0, 2.8),
        secondary_loss={
            "distribution": "PERT",
            "low": 10_000.0,
            "mode": 20_000.0,
            "high": 50_000.0,
        },
        library_pin=_pin_for(entry),
    )

    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/refresh",
        {"confirm_refresh": "1", "expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text

    await db_session.refresh(scenario)
    assert scenario.secondary_loss is None


# ---------------------------------------------------------------------------
# Case 6: pinned scenario -> 422, refresh refuses.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_refuses_on_pinned_scenario(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    entry = await _seed_published_entry(
        db_session,
        slug="refresh-pinned-scenario-entry",
        primary_loss={"distribution": "lognormal", "mean": math.log(2_000_000.0), "sigma": 0.9},
    )
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Refresh-refuses-pinned",
        organization_id=org_id,
        primary_loss=_lognormal(1_000_000.0, 1.9, stamp=_ANALYST_PIN_STAMP),
        library_pin=_pin_for(entry),
    )

    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/refresh",
        {"expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    assert r.status_code == 422, r.text
    _assert_rendered_html_error(r, "unpin")


# ---------------------------------------------------------------------------
# Case 7: entry deleted/deprecated -> flash, never 500.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_flashes_on_deprecated_entry_never_500(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    entry = await _seed_published_entry(
        db_session,
        slug="refresh-deprecated-entry",
        primary_loss={"distribution": "lognormal", "mean": math.log(2_000_000.0), "sigma": 0.9},
    )
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Refresh-deprecated-entry",
        organization_id=org_id,
        primary_loss=_lognormal(1_000_000.0, 2.5),
        library_pin=_pin_for(entry),
    )

    entry.status = "deprecated"
    db_session.add(entry)
    await db_session.commit()

    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/refresh",
        {"expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    assert r.status_code == 422, r.text
    _assert_rendered_html_error(r, "no longer available")


@pytest.mark.asyncio
async def test_refresh_flashes_on_deleted_entry_never_500(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    entry = await _seed_published_entry(
        db_session,
        slug="refresh-deleted-entry",
        primary_loss={"distribution": "lognormal", "mean": math.log(2_000_000.0), "sigma": 0.9},
    )
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Refresh-deleted-entry",
        organization_id=org_id,
        primary_loss=_lognormal(1_000_000.0, 2.5),
        library_pin=_pin_for(entry),
    )

    await db_session.delete(entry)
    await db_session.commit()

    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/refresh",
        {"expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    assert r.status_code == 422, r.text
    _assert_rendered_html_error(r, "no longer available")


# ---------------------------------------------------------------------------
# Case 8: RBAC / wrong-org / stale row_version.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_403_for_viewer_and_reviewer(
    viewer_client: AsyncClient,
    reviewer_client: AsyncClient,
) -> None:
    scenario_id = uuid.uuid4()
    for c in (viewer_client, reviewer_client):
        r = await csrf_post(
            c,
            f"/scenarios/{scenario_id}/loss/refresh",
            {"expected_row_version": "1"},
            follow_redirects=False,
        )
        assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_refresh_wrong_org_returns_404(
    authed_other_org_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    other_client, _other_org_id = authed_other_org_analyst
    entry = await _seed_published_entry(
        db_session,
        slug="refresh-wrong-org-entry",
        primary_loss={"distribution": "lognormal", "mean": math.log(2_000_000.0), "sigma": 0.9},
    )
    # No organization_id override -> defaults to seed_organization, a
    # THIRD org distinct from authed_other_org_analyst's (same shape as
    # test_pin_wrong_org_returns_404 in test_loss_pinning.py).
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Refresh-wrong-org",
        primary_loss=_lognormal(1_000_000.0, 2.5),
        library_pin=_pin_for(entry),
    )
    r = await csrf_post(
        other_client,
        f"/scenarios/{scenario.id}/loss/refresh",
        {"expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_refresh_stale_row_version_conflict(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    entry = await _seed_published_entry(
        db_session,
        slug="refresh-stale-row-version-entry",
        primary_loss={"distribution": "lognormal", "mean": math.log(2_000_000.0), "sigma": 0.9},
    )
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Refresh-stale-row-version",
        organization_id=org_id,
        primary_loss=_lognormal(1_000_000.0, 2.5),
        library_pin=_pin_for(entry),
    )
    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/refresh",
        {"expected_row_version": str(scenario.row_version + 1)},
        follow_redirects=False,
    )
    assert r.status_code == 409, r.text
    _assert_rendered_html_error(r, "reload and")


# ---------------------------------------------------------------------------
# Case 9: pin-clears-on-edit round trip -- an expert-form save clears the
# analyst_pin stamp (established re-author semantic, T3.a-disclosed), and
# the tripwire banner re-fires on the next GET. Also verifies a
# migration_recalibration stamp is equally cleared by an edit save.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_clears_on_edit_save_and_banner_refires(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Pin-clears-on-edit")
    scenario = await _get_scenario(db_session, org_id, "Pin-clears-on-edit")

    p50 = 1_000_000.0
    p95 = p50 * math.exp(2.5 * Z_0_95)
    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/pin",
        {
            "field": "primary",
            "pin_p50": str(p50),
            "pin_p95": str(p95),
            "expected_row_version": str(scenario.row_version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    await db_session.refresh(scenario)
    assert scenario.primary_loss["distribution_fit_metadata"]["sigma_recalibration"]["source"] == (
        "analyst_pin"
    )

    # The edit form renders the pinned-field advisory BEFORE the save.
    edit_before = await client.get(f"/scenarios/{scenario.id}/edit")
    assert edit_before.status_code == 200
    assert 'data-testid="pinned-field-advisory"' in edit_before.text

    # Any field save clears the pin -- submit a WIDE p5/p95 pair (ratio 500
    # => implied sigma ~= 1.89, comfortably over the 1.7 + 1e-5 tolerance)
    # so the re-authored field is ALSO wide, and the tripwire banner has
    # something to fire on.
    payload = {
        "name": scenario.name,
        "threat_category": scenario.threat_category,
        "tef_low": "0.1",
        "tef_mode": "0.5",
        "tef_high": "2.0",
        "vuln_low": "0.2",
        "vuln_mode": "0.4",
        "vuln_high": "0.6",
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "50000000",
        "expected_row_version": str(scenario.row_version),
    }
    r2 = await csrf_post(client, f"/scenarios/{scenario.id}", payload, follow_redirects=False)
    assert r2.status_code == 303, r2.text

    await db_session.refresh(scenario)
    meta = scenario.primary_loss.get("distribution_fit_metadata") or {}
    assert "sigma_recalibration" not in meta
    implied = _stored_loss_sigma(scenario.primary_loss)
    assert implied is not None and implied > 1.7 + 1e-5, implied

    view = await client.get(f"/scenarios/{scenario.id}")
    assert view.status_code == 200
    assert 'data-testid="loss-stale-wide"' in view.text


@pytest.mark.asyncio
async def test_migration_stamp_also_clears_on_edit_save(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    scenario = await _seed_scenario_with_pl(
        seed_scenario_factory,
        db_session,
        name="Migration-stamp-clears-on-edit",
        organization_id=org_id,
        primary_loss=_lognormal(1_000_000.0, 1.7, stamp=_MIGRATION_STAMP),
    )
    assert _field_has_provenance(scenario.primary_loss)

    payload = {
        "name": scenario.name,
        "threat_category": scenario.threat_category,
        "tef_low": "0.1",
        "tef_mode": "0.5",
        "tef_high": "2.0",
        "vuln_low": "0.2",
        "vuln_mode": "0.4",
        "vuln_high": "0.6",
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "1000000",  # narrow (ratio 10 => sigma ~= 0.7): the
        # assertion here is only that the STAMP is gone, not that the
        # re-authored field happens to be wide.
        "expected_row_version": str(scenario.row_version),
    }
    r = await csrf_post(client, f"/scenarios/{scenario.id}", payload, follow_redirects=False)
    assert r.status_code == 303, r.text

    await db_session.refresh(scenario)
    meta = scenario.primary_loss.get("distribution_fit_metadata") or {}
    assert "sigma_recalibration" not in meta
    assert not _field_has_provenance(scenario.primary_loss)
