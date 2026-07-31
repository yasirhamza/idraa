"""PR2 D13/D17/D19 capacity-bound epic: expert-form producer (Task 4c).

Covers `docs/superpowers/plans/2026-07-25-capacity-bound-pr2.md` Task 4c
acceptance criteria end to end via the real `/scenarios` create/edit routes:

- D17 blank+mint: a blank pl_max/sl_max mints `capacity_k * annual_revenue`
  on BOTH fields (SL via its INLINE construction site in
  ``parse_scenario_form`` — NOT ``dist_from_raw``).
- D17 hint copy: the fresh-create GET renders the pinned revenue-set /
  revenue-unset helper text, and the field itself renders BLANK (no
  pre-filled value) regardless of whether revenue is set.
- D17 explicit ceiling: a typed cap within capacity is stored as typed; a
  typed cap ABOVE capacity is rejected 422 (D13 — tighten, never loosen).
- D19 floor: a minted/typed cap at or below the distribution's p95 blocks
  with the three operator remedies wrapped around the Task-3b validator's
  factual string (same wrap the wizard uses).
- Silent-strip regression (the one that matters most): load-then-resave of
  an existing capped scenario, UNCHANGED, preserves BOTH pl_max and sl_max
  byte-for-byte — the form rebuilds the loss dict from scratch on every
  save, so an un-round-tripped field would silently strip the cap.
- Entry-currency: a typed cap in a non-USD entry currency converts on
  CREATE like its low/mode/high siblings; the EDIT path never re-converts.
- PERT losses are never capped; the field mirrors the existing
  lognormal-only kind-conditional template pattern.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.config import get_settings
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.services.fx_rates import FxRateService
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


# ---------------------------------------------------------------------------
# D17 fresh-create GET: blank field + pinned hint copy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_scenario_form_revenue_set_shows_capacity_hint_and_blank_field(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "500000000")

    r = await client.get("/scenarios/new")
    assert r.status_code == 200
    # Pinned copy (revenue set), with the org's formatted capacity. Jinja
    # autoescapes the apostrophe in prose (organization&#39;s) — assert the
    # apostrophe-free portion plus the formatted dollar figure separately.
    assert "Leave blank to use your organization" in r.text
    assert "capacity" in r.text
    assert "$500,000,000" in r.text
    assert 'name="pl_max"' in r.text
    assert 'name="sl_max"' in r.text
    # The field itself is BLANK — never a pre-filled VALUE (round-6-fixed
    # decision 2). form_defaults() sets pl_max/sl_max to "" and the mint
    # value ($500,000,000) never appears as an input value= attribute.
    assert 'value="500000000' not in r.text
    assert 'value="500,000,000' not in r.text


@pytest.mark.asyncio
async def test_new_scenario_form_revenue_unset_shows_unset_hint_with_org_link(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
) -> None:
    client, _ = authed_analyst
    r = await client.get("/scenarios/new")
    assert r.status_code == 200
    assert "Set your organization" in r.text
    assert "annual revenue to use it as the cap" in r.text
    assert 'href="/organization"' in r.text


# ---------------------------------------------------------------------------
# D17 blank+mint on save
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_blank_max_mints_capacity_on_both_pl_and_sl(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "500000000")
    payload = {
        "name": "D17-mint-both",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "5000000",
        "sl_dist": "lognormal",
        "sl_low": "10000",
        "sl_high": "500000",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303, r.text
    s = await _get_scenario(db_session, org_id, "D17-mint-both")
    expected = get_settings().capacity_k * 500_000_000.0
    assert s.primary_loss["max"] == pytest.approx(expected)
    assert s.secondary_loss is not None
    assert s.secondary_loss["max"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_create_blank_max_revenue_unset_rejected_422(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """D14: no revenue -> no invented number (the minter still refuses to
    conjure a cap). D15/Task 6 supersedes this scenario's OLD "not blocked"
    behavior, though: D15 states "When annual_revenue is NULL the
    authoring surface asks for revenue or an explicit max -- there is no
    silent rule", enforced (per D15) "at the store-time validation
    chokepoint" -- i.e. `require_loss_max` on the create call site, landed
    by Task 6. Blank field + unset revenue means the minter can produce no
    `max` AND the analyst supplied no explicit one, so the create is
    REJECTED with a 422 rather than silently persisting an uncapped
    catastrophic scenario -- closing the exact "silent rule" gap D15 names.
    (Superseded a prior version of this test pinned at Task 4c time, before
    Task 6's chokepoint enforcement landed; see the design doc's D15/D17
    rows for the full sequencing.)"""
    client, org_id = authed_analyst
    payload = {
        "name": "D17-no-revenue",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "5000000",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 422, r.text
    assert "max" in r.text.lower()
    # No silent rule (D15): the scenario is NOT persisted uncapped.
    s = (
        await db_session.execute(
            select(Scenario).where(
                Scenario.organization_id == org_id, Scenario.name == "D17-no-revenue"
            )
        )
    ).scalar_one_or_none()
    assert s is None


# ---------------------------------------------------------------------------
# D17 explicit ceiling: tighten allowed, loosen rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_typed_max_within_capacity_stored_as_typed(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "500000000")
    payload = {
        "name": "D17-typed-tighten",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "5000000",
        "pl_max": "20000000",  # well below capacity ($500M) and above p95
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303, r.text
    s = await _get_scenario(db_session, org_id, "D17-typed-tighten")
    assert s.primary_loss["max"] == pytest.approx(20_000_000.0)


@pytest.mark.asyncio
async def test_create_typed_max_exceeding_capacity_rejected_422(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """D13: an explicit override may tighten below capacity, never loosen
    above it — else a finite-but-huge cap never binds while displaying as
    bounded."""
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "500000000")
    payload = {
        "name": "D17-typed-loosen-rejected",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "5000000",
        "pl_max": "999000000",  # > $500M capacity
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 422, r.text
    assert "exceeds your organization" in r.text
    created = (
        (
            await db_session.execute(
                select(Scenario).where(Scenario.name == "D17-typed-loosen-rejected")
            )
        )
        .scalars()
        .all()
    )
    assert created == []


@pytest.mark.asyncio
async def test_create_typed_max_revenue_unset_accepted_as_is(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """No ceiling to check against when revenue is unknown -- the expert
    form is deliberately the D18 escape hatch."""
    client, org_id = authed_analyst
    payload = {
        "name": "D17-typed-no-revenue",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "5000000",
        "pl_max": "999999999999",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303, r.text
    s = await _get_scenario(db_session, org_id, "D17-typed-no-revenue")
    assert s.primary_loss["max"] == pytest.approx(999_999_999_999.0)


# ---------------------------------------------------------------------------
# D19 floor: pinned three-remedy wrap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_d19_floor_conflict_blocks_with_three_remedies(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    # p5/p95 (100_000, 5_000_000): the closed-form fit's p95 ~= the entered
    # high. A $4M revenue (cap $4M at k=1.0) sits BELOW that p95 -> D19 fires.
    await _set_annual_revenue(db_session, org_id, "4000000")
    payload = {
        "name": "D19-floor-conflict",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "5000000",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 422, r.text
    body = r.text
    assert "p95" in body or "95th percentile" in body.lower()
    assert "lower the loss estimates" in body.lower()
    assert "annual revenue" in body.lower()
    assert "expert form" in body.lower()
    created = (
        (await db_session.execute(select(Scenario).where(Scenario.name == "D19-floor-conflict")))
        .scalars()
        .all()
    )
    assert created == [], "a D19-floor-violating scenario must not be created"


# ---------------------------------------------------------------------------
# Silent-strip regression (most important) + edit pre-fill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_form_prefills_existing_pl_and_sl_max(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "500000000")
    payload = {
        "name": "D17-edit-prefill",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "5000000",
        "pl_max": "20000000",
        "sl_dist": "lognormal",
        "sl_low": "10000",
        "sl_high": "500000",
        "sl_max": "15000000",
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303, r.text
    s = await _get_scenario(db_session, org_id, "D17-edit-prefill")

    edit_r = await client.get(f"/scenarios/{s.id}/edit")
    assert edit_r.status_code == 200
    assert 'value="20,000,000"' in edit_r.text
    assert 'value="15,000,000"' in edit_r.text


@pytest.mark.asyncio
async def test_unchanged_resave_preserves_pl_and_sl_max_byte_for_byte(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """The one that matters most: the form rebuilds the loss dict from
    scratch on every save, so a max field that isn't round-tripped here
    would silently STRIP an already-authored cap on any unrelated edit."""
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "500000000")
    create_payload = {
        "name": "D17-silent-strip",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "5000000",
        "pl_max": "20000000",
        "sl_dist": "lognormal",
        "sl_low": "10000",
        "sl_high": "500000",
        "sl_max": "15000000",
    }
    r = await csrf_post(client, "/scenarios", create_payload, follow_redirects=False)
    assert r.status_code == 303, r.text
    s = await _get_scenario(db_session, org_id, "D17-silent-strip")
    sid = s.id
    row_version = s.row_version
    before_pl_max = s.primary_loss["max"]
    assert s.secondary_loss is not None
    before_sl_max = s.secondary_loss["max"]
    assert before_pl_max == pytest.approx(20_000_000.0)
    assert before_sl_max == pytest.approx(15_000_000.0)

    edit_r = await client.get(f"/scenarios/{sid}/edit")
    assert edit_r.status_code == 200
    assert 'value="20,000,000"' in edit_r.text
    assert 'value="15,000,000"' in edit_r.text

    # Resubmit the SAME values (as the edit form would echo them, 2dp per
    # format_money_input) — an unrelated no-op resave.
    edit_payload = {
        "name": "D17-silent-strip",
        "scenario_type": "custom",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "5000000",
        "pl_max": "20000000.00",
        "sl_dist": "lognormal",
        "sl_low": "10000",
        "sl_high": "500000",
        "sl_max": "15000000.00",
        "expected_row_version": str(row_version),
    }
    edit_resp = await csrf_post(client, f"/scenarios/{sid}", edit_payload, follow_redirects=False)
    assert edit_resp.status_code in (200, 302, 303), edit_resp.text[:1000]

    db_session.expire_all()
    s2 = (await db_session.execute(select(Scenario).where(Scenario.id == sid))).scalar_one()
    assert s2.primary_loss["max"] == pytest.approx(before_pl_max)
    assert s2.secondary_loss is not None
    assert s2.secondary_loss["max"] == pytest.approx(before_sl_max)


# ---------------------------------------------------------------------------
# Entry-currency: converts on CREATE, never re-converts on EDIT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_non_usd_typed_max_converted_like_siblings(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    await FxRateService(db_session).upsert_rate(
        org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 14), "SAMA", user_id=None
    )
    await db_session.commit()

    payload = {
        "name": "D17-sar-max",
        "threat_category": "ransomware",
        "entry_currency": "SAR",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "375000",
        "pl_high": "37500000",
        "pl_max": "75000000",  # SAR; /3.75 = 20,000,000 USD
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303, r.text
    s = await _get_scenario(db_session, org_id, "D17-sar-max")
    assert s.primary_loss["max"] == pytest.approx(20_000_000.0)


@pytest.mark.asyncio
async def test_edit_does_not_reconvert_max(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    """Mirrors test_scenario_edit_currency_immutable.py, extended to the
    capacity cap: entry_currency is pinned at create; the edit path must
    never call convert_loss_inputs_to_usd, or a resubmitted USD max would be
    silently divided by the rate again."""
    client, org_id = authed_admin
    await FxRateService(db_session).upsert_rate(
        org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 14), "SAMA", user_id=None
    )
    await db_session.commit()

    create_payload = {
        "name": "D17-sar-edit-noconvert",
        "threat_category": "ransomware",
        "entry_currency": "SAR",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "375000",
        "pl_high": "37500000",
        "pl_max": "75000000",
    }
    r = await csrf_post(client, "/scenarios", create_payload, follow_redirects=False)
    assert r.status_code == 303, r.text
    s = await _get_scenario(db_session, org_id, "D17-sar-edit-noconvert")
    sid = s.id
    row_version = s.row_version
    before_max = s.primary_loss["max"]
    assert before_max == pytest.approx(20_000_000.0)

    edit_payload = {
        "name": "D17-sar-edit-noconvert",
        "scenario_type": "custom",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_dist": "lognormal",
        "pl_low": "100000",
        "pl_high": "10000000",
        "pl_max": "20000000",  # the USD value the edit form displays
        "expected_row_version": str(row_version),
    }
    edit_resp = await csrf_post(client, f"/scenarios/{sid}", edit_payload, follow_redirects=False)
    assert edit_resp.status_code in (200, 302, 303), edit_resp.text[:1000]

    db_session.expire_all()
    s2 = (await db_session.execute(select(Scenario).where(Scenario.id == sid))).scalar_one()
    assert s2.primary_loss["max"] == pytest.approx(20_000_000.0), (
        f"Double-convert detected: got {s2.primary_loss['max']!r} "
        f"(would be {20_000_000.0 / 3.75:.2f} if re-divided)"
    )


# ---------------------------------------------------------------------------
# PERT losses are never capped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_pert_loss_never_stores_max_even_if_submitted(
    authed_analyst: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_analyst
    await _set_annual_revenue(db_session, org_id, "500000000")
    payload = {
        "name": "D17-pert-unaffected",
        "threat_category": "ransomware",
        **_PERT_TEF_VULN,
        "pl_low": "50000",
        "pl_mode": "250000",
        "pl_high": "2000000",
        "pl_max": "999999",  # stray field under PERT — must be ignored
    }
    r = await csrf_post(client, "/scenarios", payload, follow_redirects=False)
    assert r.status_code == 303, r.text
    s = await _get_scenario(db_session, org_id, "D17-pert-unaffected")
    assert s.primary_loss["distribution"] == "PERT"
    assert "max" not in s.primary_loss
