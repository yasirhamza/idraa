"""Wizard step-4 readout props — sigma-recal PR3 Task 2 (D22).

Asserts the SERVER-RENDERED side of the live loss-dispersion readout: that
``_build_readout_cfg`` / ``_fair_page_context`` (routes/scenarios.py) thread
``readout_cfg`` correctly into every render path that touches the Impact
page, and that ``_loss_readout.html`` embeds it as valid, parseable JSON
inside ``lossDispersionReadout(...)``. The client-side math/chart behavior
itself is covered by tests/unit/test_loss_preview_parity.py (JS parity) and
the manual Playwright checklist (PR body) — this file only proves the props
contract the JS factory depends on.
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.organization import Organization
from idraa.models.wizard_draft import WizardDraft
from tests.conftest import csrf_post

_STEP_2_DATA: dict[str, str] = {
    "name": "test-scenario-readout-props",
    "description": "wizard readout props integration test",
    "threat_category": "ransomware",
    "threat_actor_type": "cybercriminals",
    "asset_class": "systems",
}

# Matches `lossDispersionReadout({...})` for EACH pl/sl mount. Non-greedy up
# to `)'` (single-quoted x-data, per the fix documented in
# _loss_readout.html — Jinja's tojson filter escapes `'` but not `"`, so the
# JSON blob's own `"` characters are safe between single quotes but would
# break a double-quoted attribute).
_CFG_RE = re.compile(r"lossDispersionReadout\((\{.*?\})\)'", re.DOTALL)


def _extract_cfgs(html: str) -> list[dict[str, Any]]:
    return [json.loads(m) for m in _CFG_RE.findall(html)]


async def _tx_for_user(db_session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    row = (
        await db_session.execute(
            select(WizardDraft)
            .where(WizardDraft.user_id == user_id)
            .order_by(WizardDraft.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    assert row is not None, "wizard draft was not persisted by step-1/step-2 POSTs"
    return row.tx_id


async def _bootstrap_to_step_3(
    client: AsyncClient, db_session: AsyncSession, user_id: uuid.UUID
) -> uuid.UUID:
    """Steps 1-2 (skip-library path), returning the resulting tx_id."""
    await csrf_post(client, "/scenarios/new/wizard/step/1", data={"skip_library": "1"})
    await csrf_post(client, "/scenarios/new/wizard/step/2", data=_STEP_2_DATA)
    return await _tx_for_user(db_session, user_id)


def _rows_payload(fieldset: str, rows: list[tuple[str, float, float]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for idx, (name, low, high) in enumerate(rows):
        out[f"{fieldset}_sme_id_{idx}"] = ""
        out[f"{fieldset}_sme_name_{idx}"] = name
        out[f"{fieldset}_low_{idx}"] = str(low)
        out[f"{fieldset}_high_{idx}"] = str(high)
    return out


async def _post_step_3(
    client: AsyncClient,
    tx: uuid.UUID,
    *,
    tef: list[tuple[str, float, float]],
    vuln: list[tuple[str, float, float]],
) -> Any:
    # GET first so eager IRIS seeding runs (mirrors a real analyst landing on
    # the Likelihood page) before the per-page POST overwrites the rows.
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    data = {**_rows_payload("tef", tef), **_rows_payload("vuln", vuln)}
    return await csrf_post(client, f"/scenarios/new/wizard/step/3?tx={tx}", data=data)


async def _set_org_revenue(db_session: AsyncSession, org_id: uuid.UUID, revenue: str) -> None:
    org = await db_session.get(Organization, org_id)
    assert org is not None
    org.annual_revenue = Decimal(revenue)
    await db_session.commit()
    await db_session.close()


async def _resolve_analyst_id(db_session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    from idraa.models.enums import UserRole
    from idraa.models.user import User

    analyst = (
        await db_session.execute(
            select(User).where(
                User.organization_id == org_id,
                User.role == UserRole.ANALYST,
            )
        )
    ).scalar_one()
    return analyst.id


@pytest.mark.asyncio
async def test_step4_get_renders_readout_props_with_numeric_cap_and_tef_mean(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Happy path (SC-4 prop completeness): GET step 4 after steps 1-3 carry
    the live readout mount with sigmaDefault/warnThreshold/cap/currency/
    tefMean populated for BOTH the pl and sl mounts."""
    client, org_id = authed_analyst
    await _set_org_revenue(db_session, org_id, "500000000")
    user_id = await _resolve_analyst_id(db_session, org_id)

    tx = await _bootstrap_to_step_3(client, db_session, user_id)
    r3 = await _post_step_3(
        client,
        tx,
        tef=[("Alice", 1.0, 12.0)],
        vuln=[("Bob", 0.05, 0.5)],
    )
    assert r3.status_code in (302, 303), r3.text

    resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert resp.status_code == 200
    html = resp.text
    assert "lossDispersionReadout(" in html

    cfgs = _extract_cfgs(html)
    assert len(cfgs) == 2, "expected one readout mount each for pl and sl"
    by_field = {c["fieldKey"]: c for c in cfgs}
    assert set(by_field) == {"pl", "sl"}

    for field_key, cfg in by_field.items():
        assert cfg["sigmaDefault"] == pytest.approx(1.7, abs=1e-5)
        assert cfg["warnThreshold"] == pytest.approx(2.2, abs=1e-5)
        assert isinstance(cfg["cap"], (int, float))
        assert cfg["cap"] is not None
        assert cfg["currency"] == "USD"
        assert cfg["quantileBasis"] == "p5p95"
        assert cfg["mode"] == "capped_pert"  # default loss_shape, not catastrophic
        assert cfg["fieldKey"] == field_key
        assert isinstance(cfg["tefMean"], (int, float))
        assert cfg["tefMean"] is not None
        assert isinstance(cfg["vulnMean"], (int, float))
        assert cfg["vulnMean"] is not None


@pytest.mark.asyncio
async def test_step4_get_with_empty_vuln_fieldset_still_renders_with_null_tef_mean(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Fallback path: process_sme_estimates raises FinalizationError when a
    REQUIRED fieldset (tef/vuln/pl) has zero estimates. The broad except in
    _build_readout_cfg must degrade to null tefMean/vulnMean (props absent
    the ALE line, never a 500) rather than surfacing the exception."""
    client, org_id = authed_analyst
    await _set_org_revenue(db_session, org_id, "500000000")
    user_id = await _resolve_analyst_id(db_session, org_id)

    tx = await _bootstrap_to_step_3(client, db_session, user_id)
    # GET step 3 to eager-seed all four fieldsets, then explicitly submit an
    # EMPTY vuln fieldset (no vuln_* fields at all) — a malformed/incomplete
    # draft process_sme_estimates rejects with FinalizationError.
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    r3 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/3?tx={tx}",
        data=_rows_payload("tef", [("Alice", 1.0, 12.0)]),
    )
    assert r3.status_code in (302, 303), r3.text

    resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert resp.status_code == 200
    html = resp.text
    assert "lossDispersionReadout(" in html

    cfgs = _extract_cfgs(html)
    assert len(cfgs) == 2
    for cfg in cfgs:
        assert cfg.get("tefMean") is None
        assert cfg.get("vulnMean") is None
        # The rest of the props contract is unaffected by the fallback.
        assert cfg["sigmaDefault"] == pytest.approx(1.7, abs=1e-5)
        assert cfg["warnThreshold"] == pytest.approx(2.2, abs=1e-5)


@pytest.mark.asyncio
async def test_prefill_swap_partial_carries_readout_props(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """SC-6 swap-boundary regression: the HTMX prefill-from-industry POST
    re-renders `_fair_params_form_inner.html` directly (not the page shell)
    — the swapped partial must carry the SAME props as the GET-rendered
    page, or a prefill/overlay click would silently kill the live readout
    on the next keystroke."""
    client, org_id = authed_analyst
    await _set_org_revenue(db_session, org_id, "500000000")
    user_id = await _resolve_analyst_id(db_session, org_id)

    tx = await _bootstrap_to_step_3(client, db_session, user_id)
    # GET step 4 once first so IRIS eager-seeding populates all fieldsets
    # (the industry-prefill button needs an org industry/tier to act on).
    await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")

    resp = await csrf_post(
        client,
        "/scenarios/wizard/prefill-from-industry",
        data={"tx": str(tx), "page": "impact"},
    )
    assert resp.status_code == 200, resp.text
    html = resp.text
    assert "lossDispersionReadout(" in html

    cfgs = _extract_cfgs(html)
    assert len(cfgs) == 2
    by_field = {c["fieldKey"]: c for c in cfgs}
    assert set(by_field) == {"pl", "sl"}
    for cfg in by_field.values():
        assert cfg["sigmaDefault"] == pytest.approx(1.7, abs=1e-5)
        assert cfg["warnThreshold"] == pytest.approx(2.2, abs=1e-5)
        assert cfg["currency"] == "USD"
        assert isinstance(cfg["cap"], (int, float))
