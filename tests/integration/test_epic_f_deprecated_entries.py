"""Deprecated scenario-library entries: behaviour across the app (Epic F Task 5).

Covers the published-only route surfaces (browse/facets/count_published/
exports/detail), the entry-detail page's action gate + status pill, the
draft-finalize TOCTOU flash, the org-override create gate (service raises
``LibraryEntryNotFoundError`` for non-published entries -> the existing
404 mapping), and the override list's versioned entry link.

Fixtures / entry factories mirror ``tests/integration/test_library_routes.py``,
``tests/integration/test_wizard_library_prefill.py``,
``tests/integration/test_loss_pinning.py`` and
``tests/integration/test_wizard_capacity_bound.py``. The wizard-walk and
re-estimate helpers are imported directly from
``tests/integration/_wizard_step3_test_helpers.py`` and
``tests/integration/test_wizard_reestimate_finalize.py`` (an established
cross-module-import pattern in this test suite — see that module's own
imports from ``test_wizard_reestimate_routes.py``).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import (
    AssetClass,
    IndustrySubSector,
    ScenarioSource,
    ThreatActorType,
    ThreatCategory,
)
from idraa.models.organization import Organization
from idraa.models.scenario import Scenario
from idraa.models.scenario_library import ScenarioLibraryEntry, ScenarioLibraryOverride
from idraa.models.wizard_draft import WizardDraft
from idraa.repositories.scenario_library_repo import ScenarioLibraryRepo
from idraa.services.dashboard import build_dashboard
from idraa.services.scenario_library import available_facets
from tests.conftest import csrf_post
from tests.integration._wizard_step3_test_helpers import (
    _bootstrap_wizard_through_step_2,
    _current_version_token,
    _persist_fair_rows_via_steps_3_and_4,
)
from tests.integration.test_wizard_reestimate_finalize import (
    _post_re_estimate,
    _seed_scenario,
    _walk_reestimate_to_finalize,
)
from tests.integration.test_wizard_reestimate_routes import _resolve_user_id

_EXPECTED_STALE_PIN_FLASH = (
    "The library entry this draft was started from is no longer "
    "offered; cancel and start from a current entry."
)


def _make_entry(
    *,
    slug: str,
    status: str = "published",
    version: int = 1,
    entry_id: uuid.UUID | None = None,
    name: str | None = None,
    asset_class: AssetClass = AssetClass.SYSTEMS,
    sub_sectors: list[str] | None = None,
) -> ScenarioLibraryEntry:
    """Minimal-valid ScenarioLibraryEntry, mirroring conftest's
    ``seed_library_entry`` required-field set."""
    return ScenarioLibraryEntry(
        id=entry_id if entry_id is not None else uuid.uuid4(),
        version=version,
        slug=slug,
        name=name or slug,
        status=status,
        threat_event_type=ThreatCategory.RANSOMWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=asset_class,
        tags=[],
        description="Epic F Task 5 fixture entry.",
        canonical_fair_gap="Epic F Task 5 fixture; not a real gap.",
        source_citations=[],
        applicable_sub_sectors=sub_sectors,
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        suggested_control_ids=[],
    )


async def _seed_pair(
    db: AsyncSession, *, tag: str
) -> tuple[ScenarioLibraryEntry, ScenarioLibraryEntry]:
    """Seed one published + one deprecated entry with distinct slugs/names."""
    published = _make_entry(slug=f"{tag}-published", name=f"{tag.title()} Published")
    deprecated = _make_entry(
        slug=f"{tag}-deprecated", name=f"{tag.title()} Deprecated", status="deprecated"
    )
    db.add_all([published, deprecated])
    await db.commit()
    await db.refresh(published)
    await db.refresh(deprecated)
    return published, deprecated


async def _override_count(db: AsyncSession) -> int:
    return (
        await db.execute(select(func.count()).select_from(ScenarioLibraryOverride))
    ).scalar_one()


# ---------------------------------------------------------------------------
# Published-only surfaces: browse, facets, count_published, exports, detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_library_browse_lists_published_entry_only(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    published, deprecated = await _seed_pair(db_session, tag="browse")
    r = await analyst_client.get("/library")
    assert r.status_code == 200
    assert published.name in r.text
    assert deprecated.name not in r.text


@pytest.mark.asyncio
async def test_library_facets_exclude_deprecated_entries(db_session: AsyncSession) -> None:
    published = _make_entry(slug="facet-pub", status="published", asset_class=AssetClass.DATA)
    deprecated = _make_entry(
        slug="facet-dep", status="deprecated", asset_class=AssetClass.SAFETY_SYSTEMS
    )
    db_session.add_all([published, deprecated])
    await db_session.commit()

    facets = await available_facets(db_session)
    ac_values = {opt.value for opt in facets["asset_class"]}
    assert "data" in ac_values
    assert "safety_systems" not in ac_values


@pytest.mark.asyncio
async def test_count_published_excludes_deprecated(db_session: AsyncSession) -> None:
    await _seed_pair(db_session, tag="count")
    total = await ScenarioLibraryRepo(db_session).count_published()
    assert total == 1


@pytest.mark.asyncio
async def test_library_csv_and_json_export_omit_deprecated(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    published, deprecated = await _seed_pair(db_session, tag="export")

    csv_resp = await admin_client.get("/library/export.csv")
    assert csv_resp.status_code == 200
    assert published.name in csv_resp.text
    assert deprecated.name not in csv_resp.text

    json_resp = await admin_client.get("/library/export")
    assert json_resp.status_code == 200
    slugs = {e["slug"] for e in json.loads(json_resp.content)}
    assert published.slug in slugs
    assert deprecated.slug not in slugs


@pytest.mark.asyncio
async def test_library_export_one_404_for_deprecated_entry(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _published, deprecated = await _seed_pair(db_session, tag="export-one")
    r = await admin_client.get(f"/library/entries/{deprecated.id}/export")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_library_entry_detail_no_version_404_for_deprecated_entry(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _published, deprecated = await _seed_pair(db_session, tag="detail-noversion")
    r = await admin_client.get(f"/library/entries/{deprecated.id}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Detail-page action gate + status pill (?version= detail still renders)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deprecated_entry_version_detail_200_hides_wizard_and_create_override(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Spec-compliance fix round 1: the Create/Edit-override actions only
    render for ``current_user.role == "admin"`` (entry_detail.html), so an
    ``analyst_client`` run of this test is vacuous — "Create org override"
    never appears for an analyst regardless of the deprecated-entry gate.
    Must run as admin, no existing override, so the ``elif entry.status ==
    "published"`` branch is actually exercised."""
    _published, deprecated = await _seed_pair(db_session, tag="detail-version")
    r = await admin_client.get(f"/library/entries/{deprecated.id}?version={deprecated.version}")
    assert r.status_code == 200
    assert "Deprecated" in r.text
    assert "Use in wizard" not in r.text
    assert "Create org override" not in r.text
    assert (
        "Deprecated: kept for scenarios already derived from it; not offered for new scenarios."
    ) in r.text


@pytest.mark.asyncio
async def test_published_entry_version_detail_shows_both_admin_actions(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Positive control for the test above: a published entry, viewed as
    admin with no existing override, shows BOTH "Use in wizard" and "Create
    org override" — proves the gate is status-conditional, not a blanket
    admin-only suppression."""
    published, _deprecated = await _seed_pair(db_session, tag="detail-version-positive")
    r = await admin_client.get(f"/library/entries/{published.id}?version={published.version}")
    assert r.status_code == 200
    assert "Use in wizard" in r.text
    assert "Create org override" in r.text


@pytest.mark.asyncio
async def test_status_pill_colours_published_success_deprecated_warning(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    published, deprecated = await _seed_pair(db_session, tag="pill")

    r_pub = await analyst_client.get(f"/library/entries/{published.id}?version={published.version}")
    assert r_pub.status_code == 200
    assert "text-status-success" in r_pub.text

    r_dep = await analyst_client.get(
        f"/library/entries/{deprecated.id}?version={deprecated.version}"
    )
    assert r_dep.status_code == 200
    assert "text-status-warning" in r_dep.text


@pytest.mark.asyncio
async def test_draft_entry_detail_omits_deprecated_notice(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Spec-compliance NICE fix round 1: the notice text says "Deprecated",
    so it must render only for ``entry.status == "deprecated"`` — a draft
    entry (reached via ?version=, which is status-agnostic) must not show
    it even though it also hides the wizard/override actions."""
    draft = _make_entry(slug="draft-no-notice", status="draft")
    db_session.add(draft)
    await db_session.commit()
    await db_session.refresh(draft)

    r = await admin_client.get(f"/library/entries/{draft.id}?version={draft.version}")
    assert r.status_code == 200
    assert "Deprecated:" not in r.text
    assert "Use in wizard" not in r.text
    assert "Create org override" not in r.text


@pytest.mark.asyncio
async def test_deprecated_entry_detail_keeps_edit_override_link_when_override_exists(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    entry = _make_entry(slug="detail-keep-edit-override")
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(entry.id),
            "tef_low": "1.0",
            "tef_mode": "2.0",
            "tef_high": "3.0",
            "reason": "override created before deprecation",
        },
    )
    assert r.status_code in (200, 303), r.text

    entry.status = "deprecated"
    db_session.add(entry)
    await db_session.commit()

    r2 = await admin_client.get(f"/library/entries/{entry.id}?version={entry.version}")
    assert r2.status_code == 200
    assert "Edit org override" in r2.text
    assert "Create org override" not in r2.text
    assert "Use in wizard" not in r2.text


# ---------------------------------------------------------------------------
# Wizard deep-link / step-1 POST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wizard_deep_link_deprecated_entry_no_prefill_no_500(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    """Methodology NICE fix round 1 (M5-N4): pin that the wizard state is
    actually left un-prefilled, not just that the response is 200 — the
    ``contextlib.suppress(LibraryEntryNotFoundError, LibraryEntryStatusError)``
    guard (routes/scenarios.py) must fire BEFORE any ``state.*`` assignment."""
    client, org_id = authed_analyst
    _published, deprecated = await _seed_pair(db_session, tag="deep-link")
    r = await client.get(f"/scenarios/new/wizard?library_entry_id={deprecated.id}")
    assert r.status_code == 200
    assert deprecated.name not in r.text

    user_id = await _resolve_user_id(db_session, "analyst@test.local")
    draft = (
        await db_session.execute(select(WizardDraft).where(WizardDraft.user_id == user_id))
    ).scalar_one_or_none()
    assert draft is not None, "GET deep-link still persists a draft row (advance_step)"
    assert draft.organization_id == org_id
    assert draft.state_json.get("library_entry_id") is None
    assert draft.state_json.get("threat_event_frequency") is None
    assert draft.state_json.get("vulnerability") is None
    assert draft.state_json.get("primary_loss") is None


@pytest.mark.asyncio
async def test_wizard_step1_post_deprecated_entry_404(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _published, deprecated = await _seed_pair(db_session, tag="step1-post")
    r = await csrf_post(
        analyst_client,
        "/scenarios/new/wizard/step/1",
        data={"library_entry_id": str(deprecated.id)},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Draft finalize TOCTOU: entry deprecated mid-wizard-session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_finalize_flashes_when_pinned_entry_deprecated_mid_wizard(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    entry = _make_entry(slug="finalize-toctou")
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    user_id = await _resolve_user_id(db_session, "analyst@test.local")
    tx = await _bootstrap_wizard_through_step_2(client, db_session, user_id, library_entry=entry)

    # Deprecate the entry AFTER step-1 pinned it (TOCTOU window) but BEFORE
    # finalize — mirrors services/scenarios.py create_from_wizard's re-
    # validation guard.
    entry.status = "deprecated"
    db_session.add(entry)
    await db_session.commit()

    await _persist_fair_rows_via_steps_3_and_4(
        client,
        db_session,
        tx,
        tef=[("TOCTOU TEF", 1.0, 12.0)],
        vuln=[("TOCTOU Vuln", 0.05, 0.5)],
        pl=[("TOCTOU PL", 100_000.0, 5_000_000.0)],
    )

    db_session.expire_all()
    vt = await _current_version_token(db_session, tx)
    r = await csrf_post(
        client,
        f"/scenarios/new/wizard/finalize?tx={tx}",
        data={"version_token": str(vt)},
    )
    assert r.status_code == 422, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert _EXPECTED_STALE_PIN_FLASH in r.text

    draft = (
        await db_session.execute(select(WizardDraft).where(WizardDraft.tx_id == tx))
    ).scalar_one_or_none()
    assert draft is not None, "a rejected finalize must keep the draft"


# ---------------------------------------------------------------------------
# Re-estimate: never re-resolves the library entry, so finalize succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reestimate_finalize_succeeds_when_pinned_entry_deprecated(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    _published, deprecated = await _seed_pair(db_session, tag="reestimate")

    s = _seed_scenario(
        db_session,
        org_id=org_id,
        name="Reestimate pinned to deprecated entry",
        source=ScenarioSource.LIBRARY_DERIVED,
        library_pin={
            "entry_id": str(deprecated.id),
            "version": deprecated.version,
            "override_id": None,
            "override_version": None,
        },
    )
    await db_session.commit()

    tx = await _post_re_estimate(client, s.id)
    r = await _walk_reestimate_to_finalize(
        client,
        db_session,
        tx=tx,
        scenario=s,
        tef=[("Reest TEF", 1.0, 12.0)],
        vuln=[("Reest Vuln", 0.05, 0.5)],
        pl=[("Reest PL", 100_000.0, 5_000_000.0)],
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/scenarios/{s.id}"


# ---------------------------------------------------------------------------
# Refresh-from-library: "no longer available" flash, no 500; detail still 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_from_library_flashes_then_scenario_detail_still_200(
    authed_analyst: tuple[AsyncClient, uuid.UUID],
    seed_scenario_factory: Any,
    db_session: AsyncSession,
) -> None:
    client, org_id = authed_analyst
    entry = _make_entry(slug="refresh-toctou")
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    scenario = await seed_scenario_factory(
        name="Refresh pinned to deprecated entry",
        organization_id=org_id,
        library_pin={
            "entry_id": str(entry.id),
            "version": entry.version,
            "override_id": None,
            "override_version": None,
        },
    )

    entry.status = "deprecated"
    db_session.add(entry)
    await db_session.commit()

    scenario_id = scenario.id  # captured BEFORE expire_all() below
    before_tef = scenario.threat_event_frequency
    before_vuln = scenario.vulnerability
    before_pl = scenario.primary_loss
    before_sl = scenario.secondary_loss
    before_row_version = scenario.row_version

    r = await csrf_post(
        client,
        f"/scenarios/{scenario_id}/loss/refresh",
        {"expected_row_version": str(before_row_version)},
        follow_redirects=False,
    )
    assert r.status_code == 422, r.text
    assert "no longer available" in r.text

    # Methodology NICE fix round 1 (M5-N4): the soft-fail must not write
    # anything — pin the scenario's stored parameters byte-identical, not
    # just "no 500".
    db_session.expire_all()
    refreshed = await db_session.get(Scenario, scenario_id)
    assert refreshed is not None
    assert refreshed.threat_event_frequency == before_tef
    assert refreshed.vulnerability == before_vuln
    assert refreshed.primary_loss == before_pl
    assert refreshed.secondary_loss == before_sl
    assert refreshed.row_version == before_row_version

    r_detail = await client.get(f"/scenarios/{scenario_id}")
    assert r_detail.status_code == 200


# ---------------------------------------------------------------------------
# Override create gate: create_override raises LibraryEntryNotFoundError for
# non-published entries -> existing 404 mapping. Edit stays status-agnostic.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_override_on_deprecated_entry_stays_editable(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    entry = _make_entry(slug="override-stays-editable")
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(entry.id),
            "tef_low": "1.0",
            "tef_mode": "2.0",
            "tef_high": "3.0",
            "reason": "seed before deprecation",
        },
    )
    assert r.status_code in (200, 303), r.text
    override = (await db_session.execute(select(ScenarioLibraryOverride))).scalar_one()

    entry.status = "deprecated"
    db_session.add(entry)
    await db_session.commit()

    r_edit = await admin_client.get(f"/library/overrides/{override.id}/edit")
    assert r_edit.status_code == 200

    r_update = await csrf_post(
        admin_client,
        f"/library/overrides/{override.id}",
        data={
            "tef_low": "1.0",
            "tef_mode": "2.5",
            "tef_high": "4.0",
            "reason": "update after deprecation",
            "expected_version": "1",
        },
    )
    assert r_update.status_code in (200, 303), r_update.text
    await db_session.refresh(override)
    assert override.version == 2


@pytest.mark.asyncio
async def test_new_override_form_404_for_deprecated_entry(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _published, deprecated = await _seed_pair(db_session, tag="new-form")
    r = await admin_client.get(f"/library/overrides/new?entry_id={deprecated.id}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_override_404_for_deprecated_entry_no_row_written(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _published, deprecated = await _seed_pair(db_session, tag="create-gate")
    before = await _override_count(db_session)

    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(deprecated.id),
            "tef_low": "1.0",
            "tef_mode": "2.0",
            "tef_high": "3.0",
            "reason": "should not create an override on a deprecated entry",
        },
    )
    assert r.status_code == 404

    after = await _override_count(db_session)
    assert after == before


@pytest.mark.asyncio
async def test_create_override_still_creates_for_published_entry_regression(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    published, _deprecated = await _seed_pair(db_session, tag="create-regress")
    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(published.id),
            "tef_low": "1.0",
            "tef_mode": "2.0",
            "tef_high": "3.0",
            "reason": "still creates for a published entry",
        },
    )
    assert r.status_code in (200, 303), r.text
    assert await _override_count(db_session) == 1


# ---------------------------------------------------------------------------
# Override list: versioned entry link
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_list_links_deprecated_entry_with_version_and_resolves(
    admin_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    entry = _make_entry(slug="override-list-link")
    db_session.add(entry)
    await db_session.commit()
    await db_session.refresh(entry)

    r = await csrf_post(
        admin_client,
        "/library/overrides",
        data={
            "entry_id": str(entry.id),
            "tef_low": "1.0",
            "tef_mode": "2.0",
            "tef_high": "3.0",
            "reason": "seed before deprecation",
        },
    )
    assert r.status_code in (200, 303), r.text

    entry.status = "deprecated"
    db_session.add(entry)
    await db_session.commit()

    r_list = await admin_client.get("/library/overrides")
    assert r_list.status_code == 200
    expected_href = f"/library/entries/{entry.id}?version={entry.version}"
    assert expected_href in r_list.text

    r_follow = await admin_client.get(expected_href)
    assert r_follow.status_code == 200


# ---------------------------------------------------------------------------
# Dashboard sector references (published-only) exclude the deprecated entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_sector_references_exclude_deprecated_entry(
    authed_admin: tuple[AsyncClient, uuid.UUID],
    db_session: AsyncSession,
) -> None:
    _client, org_id = authed_admin
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()
    org.industry_sub_sector = IndustrySubSector.WATER_UTILITY

    published = _make_entry(
        slug="dash-published", status="published", sub_sectors=["water_utility"]
    )
    deprecated = _make_entry(
        slug="dash-deprecated", status="deprecated", sub_sectors=["water_utility"]
    )
    db_session.add_all([published, deprecated])
    await db_session.flush()

    data = await build_dashboard(db_session, org)
    assert data.scenario_coverage.reference_count == 1
