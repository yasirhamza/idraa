"""F5: page-scoped HTMX prefill + apply-overlay (likelihood/impact).

The 2026-05-28 step-3 split gives both HTMX endpoints a ``page`` form param.
Each endpoint mutates ONLY that page's fieldsets (Likelihood: TEF+Vuln scaled
by frequency_multiplier; Impact: PL+SL scaled by magnitude_multiplier) and
re-renders the shared ``_fair_params_form_inner.html`` partial scoped to that
page. No-op overlays (relevant multiplier == 1.0) are filtered from the page's
button list. A garbage ``page`` value 422s (plan-gate S-I2).

Fixtures: reuse the project's ``authed_analyst`` + ``csrf_post`` + the
``_wizard_step3_test_helpers`` bootstrap. State is inspected by reading
``WizardDraft.state_json`` directly.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.wizard_draft import WizardDraft
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _bootstrap_wizard_through_step_2,
    _seed_overlay_inline,
    _user_id_from_org,
)

pytestmark = pytest.mark.asyncio


async def _load_state_json(db: AsyncSession, tx: uuid.UUID) -> dict[str, Any]:
    draft = (
        await db.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))
    ).scalar_one_or_none()
    assert draft is not None, f"no wizard draft for tx={tx}"
    return dict(draft.state_json or {})


async def _bootstrap_seeded(
    client: AsyncClient, db_session: AsyncSession, org_id: uuid.UUID
) -> uuid.UUID:
    """Bootstrap through step 2, then GET step 3 so IRIS seeds all four
    fieldsets into ``state.sme_estimates``. Returns the tx."""
    user_id = await _user_id_from_org(db_session, org_id)
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    # Trigger eager IRIS seeding (populates tef/vuln/pl/sl).
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    return tx


async def test_apply_overlay_likelihood_scales_tef_only(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    tx = await _bootstrap_seeded(client, db_session, org_id)
    # Frequency-only overlay (mag_mult == 1.0): scales TEF, leaves PL alone.
    overlay = await _seed_overlay_inline(
        db_session,
        organization_id=org_id,
        tag="freq-only",
        display_name="Freq only",
        frequency_multiplier=2.0,
        magnitude_multiplier=1.0,
    )
    st = await _load_state_json(db_session, tx)
    tef_high_before = st["sme_estimates"]["tef"][0]["high"]
    pl_high_before = st["sme_estimates"]["pl"][0]["high"]
    await db_session.close()

    resp = await csrf_post(
        client,
        "/scenarios/wizard/apply-overlay",
        data={"tx": str(tx), "overlay_id": str(overlay.id), "page": "likelihood"},
    )
    assert resp.status_code == 200, resp.text[:300]
    after = await _load_state_json(db_session, tx)
    # tef scaled by frequency_multiplier (2.0); pl untouched on the likelihood page:
    assert after["sme_estimates"]["tef"][0]["high"] == pytest.approx(tef_high_before * 2.0)
    assert after["sme_estimates"]["pl"][0]["high"] == pl_high_before


async def test_apply_overlay_impact_scales_pl_sl_only(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    tx = await _bootstrap_seeded(client, db_session, org_id)
    # Magnitude-only overlay (freq_mult == 1.0): scales PL/SL, leaves TEF alone.
    overlay = await _seed_overlay_inline(
        db_session,
        organization_id=org_id,
        tag="mag-only",
        display_name="Mag only",
        frequency_multiplier=1.0,
        magnitude_multiplier=0.5,
    )
    st = await _load_state_json(db_session, tx)
    tef_high_before = st["sme_estimates"]["tef"][0]["high"]
    pl_high_before = st["sme_estimates"]["pl"][0]["high"]
    await db_session.close()

    resp = await csrf_post(
        client,
        "/scenarios/wizard/apply-overlay",
        data={"tx": str(tx), "overlay_id": str(overlay.id), "page": "impact"},
    )
    assert resp.status_code == 200, resp.text[:300]
    after = await _load_state_json(db_session, tx)
    assert after["sme_estimates"]["pl"][0]["high"] == pytest.approx(pl_high_before * 0.5)
    assert after["sme_estimates"]["tef"][0]["high"] == tef_high_before


async def test_likelihood_page_hides_mag_only_overlay(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    # A magnitude-only overlay (frequency_multiplier == 1.0) is a no-op on the
    # Likelihood page and must not appear in its button list (D4 filtering).
    client, org_id = authed_analyst
    tx = await _bootstrap_seeded(client, db_session, org_id)
    overlay = await _seed_overlay_inline(
        db_session,
        organization_id=org_id,
        tag="mag-only-hidden",
        display_name="MagOnlyHiddenOverlay",
        frequency_multiplier=1.0,
        magnitude_multiplier=2.0,
    )
    await db_session.close()
    resp = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert resp.status_code == 200
    assert overlay.display_name not in resp.text
    # ...but it SHOULD show on the Impact page where it materially scales PL/SL.
    impact = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert overlay.display_name in impact.text


async def test_prefill_impact_replaces_pl_sl_only(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    tx = await _bootstrap_seeded(client, db_session, org_id)
    # Mutate tef directly so we can prove prefill-impact leaves it alone.
    draft = (
        await db_session.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))
    ).scalar_one()
    sj = dict(draft.state_json)
    sme = dict(sj["sme_estimates"])
    tef_rows = [dict(r) for r in sme["tef"]]
    tef_rows[0]["high"] = 999.0
    sme["tef"] = tef_rows
    sj["sme_estimates"] = sme
    draft.state_json = sj
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(draft, "state_json")
    await db_session.commit()
    await db_session.close()

    resp = await csrf_post(
        client,
        "/scenarios/wizard/prefill-from-industry",
        data={"tx": str(tx), "page": "impact"},
    )
    assert resp.status_code == 200, resp.text[:300]
    after = await _load_state_json(db_session, tx)
    assert after["sme_estimates"]["tef"][0]["high"] == 999.0  # untouched by impact prefill


async def test_apply_overlay_rejects_invalid_page(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    # Plan-gate S-I2: a garbage `page` must 422, not silently scope to impact.
    client, org_id = authed_analyst
    tx = await _bootstrap_seeded(client, db_session, org_id)
    overlay = await _seed_overlay_inline(
        db_session,
        organization_id=org_id,
        tag="freq-bogus",
        display_name="Freq bogus",
        frequency_multiplier=2.0,
        magnitude_multiplier=1.0,
    )
    await db_session.close()
    resp = await csrf_post(
        client,
        "/scenarios/wizard/apply-overlay",
        data={"tx": str(tx), "overlay_id": str(overlay.id), "page": "bogus"},
    )
    assert resp.status_code == 422


async def test_prefill_invalid_page_rejected(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    # Plan-gate S-I2: prefill validates `page` at the top of the handler.
    client, org_id = authed_analyst
    tx = await _bootstrap_seeded(client, db_session, org_id)
    await db_session.close()
    resp = await csrf_post(
        client,
        "/scenarios/wizard/prefill-from-industry",
        data={"tx": str(tx), "page": "bogus"},
    )
    assert resp.status_code == 422


async def test_prefill_impact_does_not_materialize_absent_sl(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    # Plan-gate A-N2: when the draft has no SL fieldset, an impact prefill must
    # not write back a spurious sl key with rows. Here we delete the seeded sl
    # key, then re-prefill impact — sl must stay absent/empty (the org's IRIS
    # baseline may or may not return an SL row, so we assert it is not a
    # FABRICATED zero row: if present it came from IRIS, not from nowhere).
    client, org_id = authed_analyst
    tx = await _bootstrap_seeded(client, db_session, org_id)
    # Capture whether the org's IRIS baseline produces an SL row at all BEFORE
    # we strip it (the initial seed reflects the IRIS baseline).
    sl_seed_present = bool(
        (await _load_state_json(db_session, tx)).get("sme_estimates", {}).get("sl")
    )
    # Force the no-SL precondition: strip sl from the persisted draft.
    draft = (
        await db_session.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))
    ).scalar_one()
    sj = dict(draft.state_json)
    sme = {k: v for k, v in sj["sme_estimates"].items() if k != "sl"}
    sj["sme_estimates"] = sme
    draft.state_json = sj
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(draft, "state_json")
    await db_session.commit()
    await db_session.close()

    resp = await csrf_post(
        client,
        "/scenarios/wizard/prefill-from-industry",
        data={"tx": str(tx), "page": "impact"},
    )
    assert resp.status_code == 200, resp.text[:300]
    after = await _load_state_json(db_session, tx)
    sl_after = after["sme_estimates"].get("sl")
    if not sl_seed_present:
        # No IRIS SL baseline → impact prefill must not fabricate an sl key.
        assert not sl_after, f"impact prefill fabricated an sl row: {sl_after!r}"
    else:
        # IRIS DOES produce SL for this org — then a single IRIS-attributed row
        # is legitimate (not fabricated from a zero default).
        assert sl_after and all(r.get("low", 0) > 0 for r in sl_after)
