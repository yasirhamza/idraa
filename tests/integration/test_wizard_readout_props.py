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


# ---------------------------------------------------------------------------
# PR3 T2.a gate fixes (fix round for Task 2, commit f37ab56e)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step3_likelihood_page_emits_no_readout_cfg(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """N2(iii): the Likelihood page (step 3, tef/vuln only) must never mount
    a readout — pl/sl never render there (Arch NTH-R2-1: readout_cfg is
    built ONLY on the Impact page)."""
    client, org_id = authed_analyst
    user_id = await _resolve_analyst_id(db_session, org_id)
    tx = await _bootstrap_to_step_3(client, db_session, user_id)

    resp = await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    assert resp.status_code == 200
    assert "lossDispersionReadout(" not in resp.text


@pytest.mark.asyncio
async def test_initial_low_high_and_row_index_seed_from_last_persisted_row(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """N2(iv) + M2: initialLow/initialHigh/initialRowIndex must be asserted
    explicitly (not merely "present") — they seed the readout's first-paint
    disclosure line (M2's "previewing last saved row N") and must match the
    LAST persisted SME row for that fieldset."""
    client, org_id = authed_analyst
    await _set_org_revenue(db_session, org_id, "500000000")
    user_id = await _resolve_analyst_id(db_session, org_id)

    tx = await _bootstrap_to_step_3(client, db_session, user_id)
    r3 = await _post_step_3(client, tx, tef=[("Alice", 1.0, 12.0)], vuln=[("Bob", 0.05, 0.5)])
    assert r3.status_code in (302, 303), r3.text

    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "50000.0",
            "pl_high_0": "600000.0",
            "pl_sme_id_1": "",
            "pl_sme_name_1": "Analyst B",
            "pl_low_1": "80000.0",
            "pl_high_1": "900000.0",
        },
    )
    assert r4.status_code in (302, 303), r4.text

    resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert resp.status_code == 200
    cfgs = _extract_cfgs(resp.text)
    by_field = {c["fieldKey"]: c for c in cfgs}
    pl_cfg = by_field["pl"]
    # Last row wins (row index 1: Analyst B) — matches the "readout tracks
    # the last-focused SME row" default documented in _build_readout_cfg.
    assert pl_cfg["initialLow"] == pytest.approx(80000.0)
    assert pl_cfg["initialHigh"] == pytest.approx(900000.0)
    assert pl_cfg["initialRowIndex"] == 1


@pytest.mark.asyncio
async def test_tef_mean_matches_hand_math_of_actual_pert_triple(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """N2(i): tefMean must equal (low+4*mode+high)/6 of the ACTUAL
    process_sme_estimates(state)["tef"].pert — recomputed in-test via the
    SAME production function against the SAME persisted draft rows, side by
    side with the served cfg (hand-math verification, issue #90 rule) rather
    than an independently-invented expected value."""
    from idraa.services.wizard_finalize import process_sme_estimates
    from idraa.services.wizard_state import WizardStateService

    client, org_id = authed_analyst
    await _set_org_revenue(db_session, org_id, "500000000")
    user_id = await _resolve_analyst_id(db_session, org_id)

    tx = await _bootstrap_to_step_3(client, db_session, user_id)
    r3 = await _post_step_3(client, tx, tef=[("Alice", 1.0, 12.0)], vuln=[("Bob", 0.05, 0.5)])
    assert r3.status_code in (302, 303), r3.text

    resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert resp.status_code == 200
    cfgs = _extract_cfgs(resp.text)
    served_tef_mean = cfgs[0]["tefMean"]
    assert served_tef_mean is not None

    state = await WizardStateService(db_session).get(user_id=user_id, tx_id=tx)
    assert state is not None
    results = process_sme_estimates(state)
    p = results["tef"].pert
    expected = (p.low + 4 * p.mode + p.high) / 6.0
    assert served_tef_mean == pytest.approx(expected, rel=1e-9)


@pytest.mark.asyncio
async def test_catastrophic_mode_cfg_has_lognormal_mode_and_numeric_cap(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """N2(ii): a catastrophic loss_shape draft renders mode == "lognormal",
    a numeric cap, and the ceiling-relevant props (sigmaDefault/
    warnThreshold) needed to evaluate the capacity-ceiling state."""
    client, org_id = authed_analyst
    await _set_org_revenue(db_session, org_id, "500000000")
    user_id = await _resolve_analyst_id(db_session, org_id)

    tx = await _bootstrap_to_step_3(client, db_session, user_id)
    r3 = await _post_step_3(client, tx, tef=[("Alice", 1.0, 12.0)], vuln=[("Bob", 0.05, 0.5)])
    assert r3.status_code in (302, 303), r3.text

    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "100000.0",
            "pl_high_0": "5000000.0",
            "loss_catastrophic": "1",
        },
    )
    assert r4.status_code in (302, 303), r4.text

    resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert resp.status_code == 200
    cfgs = _extract_cfgs(resp.text)
    by_field = {c["fieldKey"]: c for c in cfgs}
    assert set(by_field) == {"pl", "sl"}
    for cfg in by_field.values():
        assert cfg["mode"] == "lognormal"
        assert isinstance(cfg["cap"], (int, float))
        assert cfg["cap"] == pytest.approx(500_000_000.0)
        assert cfg["sigmaDefault"] == pytest.approx(1.7, abs=1e-5)
        assert cfg["warnThreshold"] == pytest.approx(2.2, abs=1e-5)


@pytest.mark.asyncio
async def test_field_ceiling_exceeded_true_when_any_row_p95_meets_cap(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """M3(b): fieldCeilingExceeded must be computed over ALL persisted rows
    of the field, independent of which row the analyst currently has
    focused — a small, tight first row must not mask a wide second row
    that alone breaches the cap (D19's finalize gate rejects a submission
    when ANY row breaches, not just the previewed one)."""
    client, org_id = authed_analyst
    # cap = capacity_k(default 1.0) * revenue = $1,000,000.
    await _set_org_revenue(db_session, org_id, "1000000")
    user_id = await _resolve_analyst_id(db_session, org_id)

    tx = await _bootstrap_to_step_3(client, db_session, user_id)
    r3 = await _post_step_3(client, tx, tef=[("Alice", 1.0, 12.0)], vuln=[("Bob", 0.05, 0.5)])
    assert r3.status_code in (302, 303), r3.text

    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            # Re-gate I3 (T2.c form): THREE rows, breach in the MIDDLE —
            # tight/breach/tight fails BOTH truncation mutants ([:1] and
            # [-1:]), per the adapter N>=3 discrimination rule (a two-row
            # swap merely moves the blind spot to the other end, as the
            # T2.b micro-gate executed).
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "10000.0",
            "pl_high_0": "50000.0",  # p95 == $50k, under the cap
            "pl_sme_id_1": "",
            "pl_sme_name_1": "Analyst B",
            "pl_low_1": "100000.0",
            "pl_high_1": "5000000.0",  # p95 == $5M >= the $1M cap (MIDDLE)
            "pl_sme_id_2": "",
            "pl_sme_name_2": "Analyst C",
            "pl_low_2": "12000.0",
            "pl_high_2": "60000.0",  # p95 == $60k, under the cap (seeded row)
            "loss_catastrophic": "1",
        },
    )
    assert r4.status_code in (302, 303), r4.text

    resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert resp.status_code == 200
    cfgs = _extract_cfgs(resp.text)
    by_field = {c["fieldKey"]: c for c in cfgs}
    assert by_field["pl"]["fieldCeilingExceeded"] is True
    # sl has no persisted rows at all -> never exceeded.
    assert by_field["sl"]["fieldCeilingExceeded"] is False


@pytest.mark.asyncio
async def test_step4_get_survives_malformed_sme_row_identity(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """PR3 T4 carryover (deferred T2 NTH): the field_ceiling_exceeded walk's
    ``_dedup_latest_per_sme(...)`` call can itself raise -- its
    ``row_identity_uuid`` helper does ``UUID(str(sme_id))`` on an
    unparseable ``sme_id`` (ValueError) or ``row["sme_name"]``/
    ``.casefold()`` on a missing/non-string ``sme_name``
    (KeyError/AttributeError). That call previously sat OUTSIDE the
    per-row try in ``_build_readout_cfg``, so one corrupted row 500'd the
    WHOLE step-4 GET instead of being skipped like every other
    malformed-row case in this preview builder.

    Not reachable via the normal HTTP form path (the step-4 POST handler
    normalizes a blank ``sme_id`` to ``None`` before it ever reaches
    ``state.sme_estimates``, routes/scenarios.py:2919) -- this simulates
    draft corruption by mutating the persisted ``WizardDraft.state_json``
    directly, the same class of malformed-row defense the surrounding
    ``preview_means`` try/except already documents ("a step-4 GET must
    never 500 because pooling would reject rows finalize will flash about
    later")."""
    client, org_id = authed_analyst
    await _set_org_revenue(db_session, org_id, "1000000")
    user_id = await _resolve_analyst_id(db_session, org_id)

    tx = await _bootstrap_to_step_3(client, db_session, user_id)
    r3 = await _post_step_3(client, tx, tef=[("Alice", 1.0, 12.0)], vuln=[("Bob", 0.05, 0.5)])
    assert r3.status_code in (302, 303), r3.text

    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "10000.0",
            "pl_high_0": "50000.0",
            "loss_catastrophic": "1",
        },
    )
    assert r4.status_code in (302, 303), r4.text

    # Corrupt the persisted draft's pl[0] row identity directly -- delete
    # sme_name so row_identity_uuid's `row["sme_name"]` raises KeyError.
    # Reassign (not mutate-in-place) the JSON column so SQLAlchemy's
    # change-tracking picks it up.
    draft = (
        await db_session.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))
    ).scalar_one()
    state_json = dict(draft.state_json)
    sme_estimates = dict(state_json["sme_estimates"])
    pl_rows = [dict(r) for r in sme_estimates["pl"]]
    del pl_rows[0]["sme_name"]
    sme_estimates["pl"] = pl_rows
    state_json["sme_estimates"] = sme_estimates
    draft.state_json = state_json
    await db_session.commit()

    resp = await client.get(f"/scenarios/new/wizard/step/4?tx={tx}")
    assert resp.status_code == 200, resp.text
    cfgs = _extract_cfgs(resp.text)
    by_field = {c["fieldKey"]: c for c in cfgs}
    # No crash; the ceiling verdict degrades to False rather than raising.
    assert by_field["pl"]["fieldCeilingExceeded"] is False


@pytest.mark.asyncio
async def test_cap_precedence_prefers_existing_authored_cap_over_current_revenue(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """M4: _build_readout_cfg must mirror finalize's cap precedence — an
    already-authored/minted cap on the re-estimate TARGET wins over
    k * CURRENT org revenue. Mirrors
    test_reestimate_preserves_existing_capacity_max_after_revenue_change's
    create -> revenue-change -> re-estimate shape
    (tests/integration/test_wizard_capacity_bound.py), but asserts the
    READOUT cfg (not the finalized scenario)."""
    from idraa.models.scenario import Scenario
    from tests.integration._wizard_step3_test_helpers import (
        _bootstrap_wizard_through_step_2,
        _current_version_token,
    )
    from tests.integration.test_wizard_capacity_bound import _CAT_TEF_VULN_STEP3
    from tests.integration.test_wizard_reestimate_routes import _tx_from_location

    client, org_id = authed_analyst
    await _set_org_revenue(db_session, org_id, "500000000")
    user_id = await _resolve_analyst_id(db_session, org_id)

    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id)
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    r3 = await csrf_post(client, f"/scenarios/new/wizard/step/3?tx={tx}", data=_CAT_TEF_VULN_STEP3)
    assert r3.status_code in (302, 303), r3.text
    r4 = await csrf_post(
        client,
        f"/scenarios/new/wizard/step/4?tx={tx}",
        data={
            "pl_sme_id_0": "",
            "pl_sme_name_0": "Analyst A",
            "pl_low_0": "100000.0",
            "pl_high_0": "5000000.0",
            "loss_catastrophic": "1",
        },
    )
    assert r4.status_code in (302, 303), r4.text
    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    resp = await csrf_post(
        client, f"/scenarios/new/wizard/finalize?tx={tx}", data={"version_token": str(vt)}
    )
    assert resp.status_code == 303, resp.text
    db_session.expire_all()

    scen = (
        (await db_session.execute(select(Scenario).where(Scenario.organization_id == org_id)))
        .scalars()
        .first()
    )
    assert scen is not None
    scenario_id = scen.id
    original_max = scen.primary_loss["max"]
    assert original_max == pytest.approx(500_000_000.0)

    # Org revenue changes AFTER the scenario was authored.
    await _set_org_revenue(db_session, org_id, "900000000")

    r_re = await csrf_post(
        client, f"/scenarios/{scenario_id}/re-estimate", {}, follow_redirects=False
    )
    assert r_re.status_code == 303, r_re.text
    tx2 = uuid.UUID(_tx_from_location(r_re.headers["location"]))
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx2}")

    resp2 = await client.get(f"/scenarios/new/wizard/step/4?tx={tx2}")
    assert resp2.status_code == 200
    cfgs = _extract_cfgs(resp2.text)
    assert len(cfgs) == 2
    for cfg in cfgs:
        # The ORIGINAL (author-time) cap wins, not k * the now-current
        # $900M revenue.
        assert cfg["cap"] == pytest.approx(original_max)
        assert cfg["cap"] != pytest.approx(900_000_000.0)
