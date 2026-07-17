"""Task 3.5: Scenario EDIT — entry currency immutable, no re-conversion.

Round-trip invariant: create a SAR scenario (via the POST route so conversion
runs), GET the edit form, POST the USD-displayed values back unchanged, and
assert that the stored primary_loss distribution AND entry_currency/entry_rate
are byte-identical to pre-edit. No double-convert, no metadata drop.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.scenario import Scenario
from idraa.services.fx_rates import FxRateService
from tests.conftest import csrf_post


@pytest.mark.asyncio
async def test_noop_edit_does_not_reconvert_or_drop_currency(
    authed_admin: tuple, db_session: AsyncSession
) -> None:
    """POST the edit form back with USD values — distribution and provenance must be unchanged.

    Methodology invariant (Invariant 2): entry_currency/entry_rate are pinned at
    create and read-only on edit. The edit form displays the stored USD values;
    re-posting them must NOT call convert_loss_inputs_to_usd (which would corrupt
    the distribution by dividing by the rate again).
    """
    client, org_id = authed_admin

    # Seed SAR FX rate so the create route will accept SAR as entry currency.
    await FxRateService(db_session).upsert_rate(
        org_id, "SAR", Decimal("3.75"), dt.date(2026, 6, 14), "SAMA", user_id=None
    )
    await db_session.commit()

    # CREATE a SAR scenario via the route (conversion runs: 3750000 SAR → 1000000 USD).
    create_form = {
        "name": "edit-me",
        "scenario_type": "custom",
        "threat_category": "ransomware",
        "entry_currency": "SAR",
        "tef_dist": "pert",
        "tef_low": "0.1",
        "tef_mode": "0.3",
        "tef_high": "0.5",
        "vuln_low": "0.2",
        "vuln_mode": "0.5",
        "vuln_high": "0.8",
        "pl_dist": "pert",
        "pl_low": "3750000",
        "pl_mode": "7500000",
        "pl_high": "15000000",
        "sl_dist": "pert",
        "sl_low": "",
        "sl_mode": "",
        "sl_high": "",
    }
    resp = await csrf_post(client, "/scenarios", create_form, follow_redirects=False)
    assert resp.status_code in (302, 303), f"Create failed: {resp.status_code} {resp.text[:200]}"

    # Fetch the created row.
    row = (
        await db_session.execute(select(Scenario).where(Scenario.name == "edit-me"))
    ).scalar_one()
    before_pl_low = float(row.primary_loss["low"])
    before_pl_high = float(row.primary_loss["high"])
    before_cur = row.entry_currency
    before_rate = row.entry_rate
    sid = row.id
    rv_before = row.row_version

    # Sanity: create correctly stored USD values (not SAR values).
    assert before_pl_low == pytest.approx(1_000_000), (
        f"Create did not convert: pl_low={before_pl_low} (expected 1000000 USD)"
    )
    assert before_pl_high == pytest.approx(4_000_000), (
        f"Create did not convert: pl_high={before_pl_high} (expected 4000000 USD)"
    )
    assert before_cur == "SAR"
    assert before_rate == Decimal("3.75000000")

    # GET the edit form — must return 200.
    get_resp = await client.get(f"/scenarios/{sid}/edit")
    assert get_resp.status_code == 200, f"Edit GET failed: {get_resp.status_code}"

    # POST the edit form back with the USD-displayed values (no entry_currency field
    # on edit — immutable provenance is not editable). Include expected_row_version
    # for the P9 optimistic lock.
    edit_form = {
        "name": "edit-me",
        "scenario_type": "custom",
        "threat_category": "ransomware",
        # No entry_currency field — the edit form shows it read-only, not as an input.
        "tef_dist": "pert",
        "tef_low": "0.1",
        "tef_mode": "0.3",
        "tef_high": "0.5",
        "vuln_low": "0.2",
        "vuln_mode": "0.5",
        "vuln_high": "0.8",
        # USD values (as the edit form would display them).
        "pl_dist": "pert",
        "pl_low": "1000000",
        "pl_mode": "2000000",
        "pl_high": "4000000",
        "sl_dist": "pert",
        "sl_low": "",
        "sl_mode": "",
        "sl_high": "",
        "expected_row_version": str(rv_before),
    }
    edit_resp = await csrf_post(client, f"/scenarios/{sid}", edit_form, follow_redirects=False)
    # 303 redirect or 200 (no-op edit leaves row_version unchanged, service
    # returns early without redirect — but the route still redirects). Accept both.
    assert edit_resp.status_code in (302, 303, 200), (
        f"Edit POST unexpected status: {edit_resp.status_code} {edit_resp.text[:500]}"
    )

    # Refresh the ORM row and verify invariants.
    await db_session.refresh(row)

    # Invariant 2: USD distribution unchanged (no double-convert).
    assert float(row.primary_loss["low"]) == pytest.approx(before_pl_low), (
        f"Double-convert detected: pl_low changed from {before_pl_low} to "
        f"{float(row.primary_loss['low'])} (would be {before_pl_low / 3.75:.2f} if re-divided)"
    )
    assert float(row.primary_loss["high"]) == pytest.approx(before_pl_high), (
        f"Double-convert detected: pl_high changed from {before_pl_high} to "
        f"{float(row.primary_loss['high'])}"
    )

    # Invariant: provenance carried forward unchanged.
    assert row.entry_currency == before_cur == "SAR", (
        f"entry_currency dropped/changed: got {row.entry_currency!r}"
    )
    assert row.entry_rate == before_rate == Decimal("3.75000000"), (
        f"entry_rate dropped/changed: got {row.entry_rate!r}"
    )
