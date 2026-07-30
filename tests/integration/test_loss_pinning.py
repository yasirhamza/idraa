"""Analyst loss-quantile pin/unpin (sigma-recal PR3 Task 3, D20/D21).

Per docs/superpowers/plans/2026-07-30-sigma-recal-pr3.md Task 3 Step 1.

Fixture revenue pinned explicitly (I-M5): org annual_revenue = $4,000,000,000
and p50 = $1,000,000 -> admissible sigma ceiling
ln(cap/p50)/z_0.95 = ln(4000)/1.6448536269514722 ~= 5.04 (executed below), so
sigma up to ~5 clears the D19 capacity floor by construction -- pins used in
the non-D19 test cases stay comfortably under that ceiling. The D19-specific
rejection case (case 5's last assertion) uses its OWN small-revenue fixture
so the floor is deliberately tripped.

``field=banana`` on both routes is rejected 422 by FastAPI's own
``Literal["primary", "secondary"]`` Form-field validation (Sec-I3) --
no service-level branch needed for that case.
"""

from __future__ import annotations

import importlib.util
import math
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fair_cam.quantile_pooling import Z_0_95
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import idraa
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.services import loss_pinning
from tests.conftest import csrf_post

_PERT_TEF_VULN = {
    "tef_dist": "pert",
    "tef_low": "0.1",
    "tef_mode": "0.5",
    "tef_high": "2.0",
    "vuln_low": "0.2",
    "vuln_mode": "0.4",
    "vuln_high": "0.6",
}

# Independently-typed copy of a fully-populated 16-key fit record (mirrors
# tests/migrations/test_sigma_recalibration_migration.py's own
# _FULL_FIT_METADATA constant) -- used to prove the pin service moves EVERY
# key under superseded_fit, never a partial subset (N-4 full-partition check).
_FULL_FIT_METADATA: dict[str, Any] = {
    "pooled_meanlog": 14.5,
    "pooled_sdlog": 2.9,
    "component_meanlogs": [14.5],
    "component_sdlogs": [2.9269],
    "pooling_method": "linear_opinion_pool_v1",
    "pooled_min_support": 0.0,
    "pooled_max_support": None,
    "q_low_quantile": 0.05,
    "q_high_quantile": 0.95,
    "n_smes": 1,
    "sme_ids": ["11111111-1111-1111-1111-111111111111"],
    "weights": [1.0],
    "source": "quantile_lognormal_pool",
    "fitter": "lognorm_native",
    "fitted_at": "2026-07-08T10:22:00+00:00",
    "schema_version": 3,
}


def _versions_dir() -> Path:
    return Path(idraa.__file__).resolve().parent.parent.parent / "alembic" / "versions"


def _import_migration_module(revision_prefix: str) -> Any:
    """spec_from_file_location idiom, copied from
    tests/migrations/test_sigma_recalibration_migration.py:99-105."""
    mig_path = next(_versions_dir().glob(f"{revision_prefix}_*.py"))
    spec = importlib.util.spec_from_file_location(f"_mig_{revision_prefix}", mig_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _set_annual_revenue(db: AsyncSession, org_id: uuid.UUID, revenue: str | None) -> None:
    org = await db.get(Organization, org_id)
    assert org is not None
    org.annual_revenue = Decimal(revenue) if revenue is not None else None
    await db.commit()


async def _get_scenario(db: AsyncSession, org_id: uuid.UUID, name: str) -> Scenario:
    return (
        await db.execute(
            select(Scenario).where(Scenario.organization_id == org_id, Scenario.name == name)
        )
    ).scalar_one()


async def _create_lognormal_scenario(
    client: AsyncClient,
    *,
    name: str,
    pl_low: str = "100000",
    pl_high: str = "1000000",
    sl_dist: str | None = None,
    sl_low: str | None = None,
    sl_high: str | None = None,
) -> Any:
    payload: dict[str, str] = {
        "name": name,
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": pl_low,
        "pl_high": pl_high,
    }
    if sl_dist is not None:
        payload["sl_dist"] = sl_dist
        payload["sl_low"] = sl_low or ""
        payload["sl_high"] = sl_high or ""
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r


# ---------------------------------------------------------------------------
# Case 1: pin primary -- mean/sigma/max/stamp shape, superseded_fit full move
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_primary_stores_median_anchored_fit_and_mints_cap(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")  # $4B (I-M5)
    await _create_lognormal_scenario(client, name="Pin-primary-happy")
    scenario = await _get_scenario(db_session, org_id, "Pin-primary-happy")

    # Seed a full 16-key fit record on primary_loss so the superseded_fit
    # move can be proven complete (N-4 full-partition check).
    scenario.primary_loss = {
        **scenario.primary_loss,
        "distribution_fit_metadata": dict(_FULL_FIT_METADATA),
    }
    await db_session.commit()
    await db_session.refresh(scenario)
    expected_row_version = scenario.row_version

    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/pin",
        {
            "field": "primary",
            "pin_p50": "1000000",
            "pin_p95": "8000000",
            "expected_row_version": str(expected_row_version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/scenarios/{scenario.id}?pinned=1"

    await db_session.refresh(scenario)
    pl = scenario.primary_loss
    assert pl["distribution"] == "lognormal"
    # D2 median-anchor semantics: at q_low=0.5, mu is ANALYTICALLY ln(p50).
    assert pl["mean"] == pytest.approx(math.log(1_000_000.0), rel=1e-12)
    assert pl["sigma"] == pytest.approx(math.log(8.0) / Z_0_95, rel=1e-12)
    # No prior max -> minted from the $4B org revenue (capacity_k default 1.0).
    assert pl["max"] == pytest.approx(4_000_000_000.0)

    meta = pl["distribution_fit_metadata"]
    stamp = meta["sigma_recalibration"]
    assert stamp["source"] == "analyst_pin"
    assert set(stamp.keys()) == {
        "source",
        "pinned_at",
        "actor_id",
        "input",
        "prior_sigma",
        "prior_source",
    }
    assert stamp["input"] == {"p50": 1_000_000.0, "p95": 8_000_000.0}

    # Full 16-key prior fit record moved under superseded_fit, none dropped,
    # none left at top level.
    superseded = meta["superseded_fit"]
    assert set(superseded.keys()) == set(loss_pinning.FIT_RECORD_KEYS)
    assert superseded == _FULL_FIT_METADATA
    for key in loss_pinning.FIT_RECORD_KEYS:
        assert key not in meta

    # Audit row.
    from idraa.models.audit_log import AuditLog

    audit_row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_id == scenario.id, AuditLog.action == "scenario.loss_pinned"
            )
        )
    ).scalar_one()
    assert audit_row.changes["field"] == [None, "primary"]
    assert audit_row.changes["expected_row_version"] == [None, expected_row_version]
    assert "primary_loss" in audit_row.changes


# ---------------------------------------------------------------------------
# Case 2: migration round-trip -- both migrations' skip-guards honor the pin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_dist_is_untouched_by_both_migration_helpers(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Pin-migration-roundtrip")
    scenario = await _get_scenario(db_session, org_id, "Pin-migration-roundtrip")

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
    await db_session.refresh(scenario)
    pinned_dist = dict(scenario.primary_loss)

    sigma_mig = _import_migration_module("c4e4d441087c")
    tef_mig = _import_migration_module("b3f8a2d94c1e")

    assert sigma_mig._recalibrate_dist(pinned_dist) is None
    assert tef_mig._collapse_tef(pinned_dist) is None
    # FIT_RECORD_KEYS never drifts from the migration's own copy (N-4).
    assert loss_pinning.FIT_RECORD_KEYS == tef_mig._FIT_RECORD_KEYS


# ---------------------------------------------------------------------------
# Case 3: banner suppression on a pinned wide field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_page_suppresses_stale_banner_for_pinned_wide_field(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Pin-banner-suppress")
    scenario = await _get_scenario(db_session, org_id, "Pin-banner-suppress")

    # sigma = 2.5 implies p95/p50 = exp(2.5 * Z_0_95).
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

    view = await client.get(f"/scenarios/{scenario.id}?loss_wide=1")
    assert view.status_code == 200
    assert 'data-testid="loss-stale-wide"' not in view.text


# ---------------------------------------------------------------------------
# Case 4: warn-badge passthrough -- pinning a wide sigma still succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_with_sigma_above_warn_threshold_succeeds(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Pin-warn-badge")
    scenario = await _get_scenario(db_session, org_id, "Pin-warn-badge")

    p50 = 1_000_000.0
    p95 = 50_000_000.0  # implied sigma = ln(50)/Z_0_95 ~= 2.378 > 2.2 warn, < ~5.04 D19 ceiling
    implied_sigma = math.log(p95 / p50) / Z_0_95
    assert implied_sigma > 2.2

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
    assert scenario.primary_loss["sigma"] == pytest.approx(implied_sigma, rel=1e-12)


# ---------------------------------------------------------------------------
# Case 5: rejections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pin_rejects_non_positive_p50(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Pin-reject-p50")
    scenario = await _get_scenario(db_session, org_id, "Pin-reject-p50")
    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/pin",
        {
            "field": "primary",
            "pin_p50": "0",
            "pin_p95": "8000000",
            "expected_row_version": str(scenario.row_version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_pin_rejects_p95_at_or_below_p50(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Pin-reject-inverted")
    scenario = await _get_scenario(db_session, org_id, "Pin-reject-inverted")
    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/pin",
        {
            "field": "primary",
            "pin_p50": "1000000",
            "pin_p95": "1000000",
            "expected_row_version": str(scenario.row_version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_pin_rejects_non_finite_string(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Pin-reject-nonfinite")
    scenario = await _get_scenario(db_session, org_id, "Pin-reject-nonfinite")
    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/pin",
        {
            "field": "primary",
            "pin_p50": "not-a-number",
            "pin_p95": "8000000",
            "expected_row_version": str(scenario.row_version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_pin_and_unpin_reject_field_banana(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Pin-reject-field-banana")
    scenario = await _get_scenario(db_session, org_id, "Pin-reject-field-banana")
    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/pin",
        {
            "field": "banana",
            "pin_p50": "1000000",
            "pin_p95": "8000000",
            "expected_row_version": str(scenario.row_version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 422, r.text
    r2 = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/unpin",
        {"field": "banana", "expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    assert r2.status_code == 422, r2.text


@pytest.mark.asyncio
async def test_pin_rejects_mixture_field_naming_reestimate(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Pin-reject-mixture")
    scenario = await _get_scenario(db_session, org_id, "Pin-reject-mixture")
    scenario.primary_loss = {
        "distribution": "lognormal_mixture",
        "components": [
            {"mean": math.log(1_000_000.0), "sigma": 1.7, "weight": 0.5},
            {"mean": math.log(2_000_000.0), "sigma": 1.7, "weight": 0.5},
        ],
    }
    await db_session.commit()
    await db_session.refresh(scenario)
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
    assert r.status_code == 422, r.text
    assert "Re-estimate" in r.text


@pytest.mark.asyncio
async def test_pin_and_unpin_403_for_viewer_and_reviewer(
    analyst_client: AsyncClient,
    viewer_client: AsyncClient,
    reviewer_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    scenario_id = uuid.uuid4()
    data = {
        "field": "primary",
        "pin_p50": "1000000",
        "pin_p95": "8000000",
        "expected_row_version": "1",
    }
    for c in (viewer_client, reviewer_client):
        r = await csrf_post(c, f"/scenarios/{scenario_id}/loss/pin", data, follow_redirects=False)
        assert r.status_code == 403, r.text
        r2 = await csrf_post(
            c,
            f"/scenarios/{scenario_id}/loss/unpin",
            {"field": "primary", "expected_row_version": "1"},
            follow_redirects=False,
        )
        assert r2.status_code == 403, r2.text


@pytest.mark.asyncio
async def test_pin_wrong_org_returns_404(
    authed_other_org_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    # NOTE: does NOT also request authed_analyst — authed_analyst and
    # authed_other_org_analyst both mutate the SAME shared httpx
    # AsyncClient's cookie jar (both fixtures depend on the `client`
    # fixture and call client.cookies.set(SESSION_COOKIE, ...)); requesting
    # both in one test silently re-authenticates the "first" client as the
    # SECOND fixture's user. The target scenario is built directly via the
    # seed_scenario_factory ORM fixture instead (belongs to
    # seed_organization, a THIRD, distinct org) so only one HTTP-client
    # fixture is in play.
    other_client, _other_org_id = authed_other_org_analyst
    scenario = await seed_scenario_factory(name="Pin-wrong-org")
    # seed_scenario_factory's own primary_loss kwarg collides with its
    # PERT default (TypeError: multiple values) -- set the lognormal shape
    # via a follow-up mutation on the SAME db_session instead.
    scenario.primary_loss = {
        "distribution": "lognormal",
        "mean": math.log(1_000_000.0),
        "sigma": 1.7,
        "max": 4_000_000_000.0,
    }
    await db_session.commit()
    await db_session.refresh(scenario)
    r = await csrf_post(
        other_client,
        f"/scenarios/{scenario.id}/loss/pin",
        {
            "field": "primary",
            "pin_p50": "1000000",
            "pin_p95": "8000000",
            "expected_row_version": str(scenario.row_version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_pin_stale_row_version_conflict(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Pin-stale-version")
    scenario = await _get_scenario(db_session, org_id, "Pin-stale-version")
    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/pin",
        {
            "field": "primary",
            "pin_p50": "1000000",
            "pin_p95": "8000000",
            "expected_row_version": str(scenario.row_version + 999),
        },
        follow_redirects=False,
    )
    assert r.status_code in (409, 422), r.text


@pytest.mark.asyncio
async def test_pin_rejects_pert_field(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    payload = {
        "name": "Pin-reject-pert",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "pert",
        "pl_low": "100000",
        "pl_mode": "300000",
        "pl_high": "1000000",
    }
    r0 = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r0.status_code == 303, r0.text
    scenario = await _get_scenario(db_session, org_id, "Pin-reject-pert")
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
    assert r.status_code == 422, r.text
    assert "lognormal loss fields" in r.text


@pytest.mark.asyncio
async def test_pin_rejects_when_implied_sigma_exceeds_d19_ceiling(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """D19 floor: existing_max preserved from a small-revenue create
    ($4,000,000 cap) is far below a subsequent pin's implied p95
    ($50,000,000) -> the capacity-floor validator rejects with the
    wrapped three-remedy copy (wrap_d19_floor_message)."""
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000")  # $4M -> cap $4M
    # Create with a small p95 so the mint succeeds (cap $4M > create-time p95).
    await _create_lognormal_scenario(
        client, name="Pin-d19-ceiling", pl_low="10000", pl_high="100000"
    )
    scenario = await _get_scenario(db_session, org_id, "Pin-d19-ceiling")
    assert scenario.primary_loss["max"] == pytest.approx(4_000_000.0)

    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/pin",
        {
            "field": "primary",
            "pin_p50": "1000000",
            "pin_p95": "50000000",  # p95 > preserved $4M cap
            "expected_row_version": str(scenario.row_version),
        },
        follow_redirects=False,
    )
    assert r.status_code == 422, r.text
    body = r.text.lower()
    assert "lower the loss estimates" in body
    assert "annual revenue" in body
    assert "expert form" in body


# ---------------------------------------------------------------------------
# Case 6: unpin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpin_removes_stamp_keeps_mean_sigma_max_byte_unchanged(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Unpin-happy")
    scenario = await _get_scenario(db_session, org_id, "Unpin-happy")
    pin_r = await csrf_post(
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
    assert pin_r.status_code == 303, pin_r.text
    await db_session.refresh(scenario)
    mean_before = scenario.primary_loss["mean"]
    sigma_before = scenario.primary_loss["sigma"]
    max_before = scenario.primary_loss["max"]

    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/unpin",
        {"field": "primary", "expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/scenarios/{scenario.id}?unpinned=1"

    await db_session.refresh(scenario)
    pl = scenario.primary_loss
    assert pl["mean"] == mean_before
    assert pl["sigma"] == sigma_before
    assert pl["max"] == max_before
    assert "sigma_recalibration" not in (pl.get("distribution_fit_metadata") or {})

    from idraa.models.audit_log import AuditLog

    audit_row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_id == scenario.id, AuditLog.action == "scenario.loss_unpinned"
            )
        )
    ).scalar_one()
    assert audit_row.changes["field"] == [None, "primary"]


@pytest.mark.asyncio
async def test_unpin_on_unpinned_field_rejected_422(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Unpin-not-pinned")
    scenario = await _get_scenario(db_session, org_id, "Unpin-not-pinned")
    r = await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/unpin",
        {"field": "primary", "expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    assert r.status_code == 422, r.text


@pytest.mark.xfail(
    reason="banner re-fire on unpin is Task 4's tripwire (_loss_stale_wide); "
    "not implemented until Task 4 lands",
    strict=False,
)
@pytest.mark.asyncio
async def test_unpin_on_wide_field_refires_tripwire_banner_on_next_get(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Unpin-wide-refire")
    scenario = await _get_scenario(db_session, org_id, "Unpin-wide-refire")
    p50 = 1_000_000.0
    p95 = p50 * math.exp(2.5 * Z_0_95)
    await csrf_post(
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
    await db_session.refresh(scenario)
    await csrf_post(
        client,
        f"/scenarios/{scenario.id}/loss/unpin",
        {"field": "primary", "expected_row_version": str(scenario.row_version)},
        follow_redirects=False,
    )
    view = await client.get(f"/scenarios/{scenario.id}?loss_wide=1")
    assert 'data-testid="loss-stale-wide"' in view.text


# ---------------------------------------------------------------------------
# Case 8: expert-form props (SC-4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_form_renders_readout_and_pin_panel_with_policy_props(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "4000000000")
    await _create_lognormal_scenario(client, name="Edit-props-happy")
    scenario = await _get_scenario(db_session, org_id, "Edit-props-happy")

    r = await client.get(f"/scenarios/{scenario.id}/edit")
    assert r.status_code == 200, r.text
    assert "lossDispersionReadout(" in r.text
    assert '"sigmaDefault": 1.7' in r.text
    assert '"warnThreshold": 2.2' in r.text
    assert '"currency": "USD"' in r.text
    assert '"cap": 4000000000.0' in r.text
    # D17-hint side effect: capacity_max now reaches the edit context too.
    assert "capacity" in r.text.lower()
