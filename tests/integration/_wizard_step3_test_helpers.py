"""Shared wizard step-3 test helpers extracted from the (now-deleted)
``tests/integration/test_wizard_step3_helpers.py`` module.

The original module bundled three reusable async helpers alongside a set
of PERT-triple form assertions. T11's reshape replaced the PERT form
shape with SME-row estimates, making the in-module tests obsolete (they
were deleted in the T11 follow-up). The three helpers themselves remain
useful — ``tests/integration/test_concurrent_wizard_sessions.py``
imports them to set up its hard-delete race regression. Moving them to
a leading-underscore module signals "test helpers, not a test module"
to pytest's collection while keeping a stable import path for callers.

The helpers intentionally do NOT assert against PERT field names — they
just drive the wizard through step 2 and seed an overlay row. They are
shape-agnostic and will continue to work as the wizard form evolves.
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.wizard_draft import WizardDraft
from tests.conftest import csrf_post


async def _bootstrap_wizard_through_step_2(
    client: AsyncClient,
    db_session: AsyncSession,
    user_id: uuid.UUID,
    *,
    library_entry: Any = None,
) -> uuid.UUID:
    """Drive the wizard from /scenarios/new to the end of step 2; return tx_id.

    Posts step 1 (skip-library path so the test does not require a seeded
    library entry) then step 2 with the scenario metadata fields.

    Returns the tx_id of the resulting wizard draft so the caller can
    re-inject it into subsequent POSTs.
    """
    if library_entry is not None:
        await csrf_post(
            client,
            "/scenarios/new/wizard/step/1",
            data={"library_entry_id": str(library_entry.id)},
        )
    else:
        await csrf_post(
            client,
            "/scenarios/new/wizard/step/1",
            data={"skip_library": "1"},
        )
    step2_data: dict[str, str] = {
        "name": "test-scenario-pi-f7",
        "description": "wizard step3 helper integration test",
        "threat_category": "ransomware",
        "threat_actor_type": "cybercriminals",
        "asset_class": "systems",
    }
    await csrf_post(client, "/scenarios/new/wizard/step/2", data=step2_data)

    # Look up the draft tx_id for this user.
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


async def _current_version_token(db_session: AsyncSession, tx: uuid.UUID) -> int:
    """Read the live version_token off the wizard draft for ``tx``.

    The per-page step-3/4/5 POSTs each bump the token via advance_step's
    blind-write path, so the finalize POST must read it fresh. Callers close
    the session first when the app engine has committed concurrently so SQLite
    doesn't serve a stale snapshot.
    """
    draft = (
        await db_session.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))
    ).scalar_one()
    return draft.version_token


async def _persist_fair_rows_via_steps_3_and_4(
    client: AsyncClient,
    db_session: AsyncSession,
    tx: uuid.UUID,
    *,
    tef: list[tuple[str, float, float]],
    vuln: list[tuple[str, float, float]],
    pl: list[tuple[str, float, float]],
    sl: list[tuple[str, float, float]] | None = None,
) -> None:
    """Persist SME rows into ``state.sme_estimates`` via the per-page POSTs.

    2026-05-28 step-3 split (D6): finalize is state-sourced. Each row is a
    ``(sme_id_or_name, low, high)`` triple. When the first element parses as a
    UUID it is submitted as ``<fieldset>_sme_id_<n>``; otherwise as
    ``<fieldset>_sme_name_<n>`` (free-text). Step 3 persists TEF+Vuln, step 4
    persists PL+SL — each its own per-page POST so the merge-doesn't-clobber
    path is exercised exactly as the real wizard drives it.
    """

    def _rows_payload(fieldset: str, rows: list[tuple[str, float, float]]) -> dict[str, str]:
        out: dict[str, str] = {}
        for idx, (ident, low, high) in enumerate(rows):
            try:
                uuid.UUID(ident)
                out[f"{fieldset}_sme_id_{idx}"] = ident
                out[f"{fieldset}_sme_name_{idx}"] = ""
            except ValueError:
                out[f"{fieldset}_sme_id_{idx}"] = ""
                out[f"{fieldset}_sme_name_{idx}"] = ident
            out[f"{fieldset}_low_{idx}"] = str(low)
            out[f"{fieldset}_high_{idx}"] = str(high)
        return out

    # GET step 3 first so eager IRIS seeding populates all four fieldsets
    # (mirrors a real analyst landing on the Likelihood page).
    await client.get(f"/scenarios/new/wizard/step/3?tx={tx}")
    step3_data = {**_rows_payload("tef", tef), **_rows_payload("vuln", vuln)}
    r3 = await csrf_post(client, f"/scenarios/new/wizard/step/3?tx={tx}", data=step3_data)
    assert r3.status_code in (302, 303), f"step-3 POST failed: {r3.status_code}: {r3.text}"
    step4_data = {**_rows_payload("pl", pl), **_rows_payload("sl", sl or [])}
    r4 = await csrf_post(client, f"/scenarios/new/wizard/step/4?tx={tx}", data=step4_data)
    assert r4.status_code in (302, 303), f"step-4 POST failed: {r4.status_code}: {r4.text}"


async def _user_id_from_org(db_session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    """Resolve the analyst user id by joining org id (single-user-per-org test setup)."""
    from idraa.models.user import User

    row = (
        await db_session.execute(
            select(User).where(User.organization_id == org_id, User.email == "analyst@test.local")
        )
    ).scalar_one_or_none()
    assert row is not None, "analyst user not found for org"
    return row.id


async def _seed_overlay_inline(
    db_session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    tag: str = "ransomware-uplift",
    display_name: str = "Ransomware uplift",
    frequency_multiplier: float = 2.0,
    magnitude_multiplier: float = 3.0,
    is_active: bool = True,
) -> Any:
    """Seed an OverlayDefinition + matching v1 revision with explicit multipliers.

    Mirrors ``seed_overlay`` in ``tests/conftest.py`` but takes the multiplier
    kwargs directly so callers can exercise apply-overlay with known
    factors (and ``is_active=False`` for the soft-delete regression test).
    """
    from idraa.models.overlay import OverlayDefinition, OverlayDefinitionRevision

    od = OverlayDefinition(
        organization_id=organization_id,
        tag=tag,
        display_name=display_name,
        version=1,
        is_active=is_active,
        frequency_multiplier=frequency_multiplier,
        magnitude_multiplier=magnitude_multiplier,
        methodology="Apply-overlay test methodology; twenty-char min met.",
        sources=["unit_test"],
    )
    db_session.add(od)
    await db_session.flush()
    rev = OverlayDefinitionRevision(
        overlay_definition_id=od.id,
        version=1,
        tag=tag,
        display_name=display_name,
        frequency_multiplier=frequency_multiplier,
        magnitude_multiplier=magnitude_multiplier,
        methodology=od.methodology,
        sources=list(od.sources),
        methodology_change_reason="Initial overlay",
    )
    db_session.add(rev)
    await db_session.commit()
    return od
