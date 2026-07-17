"""GET /library + entry detail + HTMX partials + RBAC.

Spec §8.1 §8.3.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario_library import ScenarioLibraryEntry


@pytest.mark.asyncio
async def test_get_library_returns_card_grid(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    r = await analyst_client.get("/library")
    assert r.status_code == 200
    # Renders the library entry's name in the page
    assert seed_library_entry.name in r.text
    # Has the filter sidebar
    assert "filter" in r.text.lower()


@pytest.mark.asyncio
async def test_get_library_filters_by_threat_actor(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """F14 carryover C: seed a second entry with a DIFFERENT actor type,
    filter by cybercriminals, and assert only the matching entry appears."""
    # seed_library_entry is threat_actor_type=CYBERCRIMINALS.
    # Add a nation_state entry that should be excluded by the filter.
    nation_state_entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="nation-state-entry-carryover-c",
        name="Nation State Entry — carryover C",
        status="published",
        threat_event_type=ThreatCategory.MALWARE,
        threat_actor_type=ThreatActorType.NATION_STATE,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="Nation-state actor entry for filter-exclusion test.",
        canonical_fair_gap="Nation-state FAIR gap test.",
        source_citations=[],
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
    db_session.add(nation_state_entry)
    await db_session.commit()

    r = await analyst_client.get("/library?threat_actor_type=cybercriminals")
    assert r.status_code == 200
    # Matching entry appears in results
    assert seed_library_entry.name in r.text
    # Non-matching nation-state entry must NOT appear
    assert nation_state_entry.name not in r.text


@pytest.mark.asyncio
async def test_get_library_pagination(
    analyst_client: AsyncClient,
    seed_library_entries_factory: Callable[..., Awaitable[list[Any]]],
) -> None:
    """30 entries; default page-size 50; one page.

    F14 carryover B: assert at least the first seeded entry name appears.
    """
    await seed_library_entries_factory(count=30)
    r = await analyst_client.get("/library?page=1")
    assert r.status_code == 200
    # Carryover B: verify seeded entries are rendered, not just a 200 response.
    assert "Library Entry 000" in r.text


@pytest.mark.asyncio
async def test_get_library_rejects_non_positive_page(
    analyst_client: AsyncClient,
) -> None:
    """page=0 and page=-1 must produce 422 (FastAPI Query(ge=1) validation)."""
    r0 = await analyst_client.get("/library?page=0")
    r_neg = await analyst_client.get("/library?page=-5")
    rp = await analyst_client.get("/library/_partials/cards?page=0")
    assert r0.status_code == 422, f"page=0 expected 422, got {r0.status_code}"
    assert r_neg.status_code == 422, f"page=-5 expected 422, got {r_neg.status_code}"
    assert rp.status_code == 422, f"partial page=0 expected 422, got {rp.status_code}"


@pytest.mark.asyncio
async def test_get_library_entry_detail(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    r = await analyst_client.get(f"/library/entries/{seed_library_entry.id}")
    assert r.status_code == 200
    assert seed_library_entry.canonical_fair_gap in r.text


@pytest.mark.asyncio
async def test_get_library_partial_for_htmx(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    """HTMX hx-get on filter change returns the cards-only partial, not the shell."""
    r = await analyst_client.get(
        "/library/_partials/cards",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    # Partial should not include the full layout/header
    assert "<html" not in r.text.lower()
    assert seed_library_entry.name in r.text


@pytest.mark.asyncio
async def test_get_library_403_for_unauthenticated(
    anonymous_client: AsyncClient,
) -> None:
    r = await anonymous_client.get("/library")
    assert r.status_code in (302, 303, 307, 401, 403)


@pytest.mark.asyncio
async def test_library_browse_search_and_filters_share_one_form(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    """Issue #264: the search input (name="q") and the sidebar filter
    checkboxes must resolve to the SAME single <form>.

    ``_filter_sidebar.html`` previously opened its own ``<form>`` while being
    ``{% include %}``-ed inside ``library-browse-form``. Per HTML5 tree
    construction the inner ``<form>`` start tag is dropped, but the inner
    ``</form>`` closes the OUTER form, ejecting ``q`` outside any form. Search
    and facets then submit as separate HTMX requests, never combined.

    Parsed with html5lib (the HTML5-spec-compliant parser) so the assertion
    reflects real browser tree construction, not html.parser's lenient nesting.
    """
    from bs4 import BeautifulSoup

    r = await analyst_client.get("/library")
    assert r.status_code == 200

    soup = BeautifulSoup(r.text, "html5lib")

    q_input = soup.find("input", attrs={"name": "q"})
    assert q_input is not None, "search input (name='q') not rendered"

    q_form = q_input.find_parent("form")
    assert q_form is not None, (
        "search input is not inside any <form> — nested sidebar <form> ejected "
        "it via HTML5 tree construction (issue #264)"
    )

    # The search input's OWN form (the desktop browse form) must also contain
    # the sidebar facet checkboxes, so hx-include='closest form' combines them.
    # (The page also renders the sidebar inside a separate mobile-only form, so
    # we scope the facet lookup to q's form rather than the whole document.)
    facet = q_form.find("input", attrs={"name": "threat_actor_type"})
    assert facet is not None, (
        "search input and filter checkboxes resolve to DIFFERENT forms — "
        "hx-include='closest form' will not combine them (issue #264)"
    )


@pytest.mark.asyncio
async def test_library_filter_sidebar_is_not_a_form(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    """Issue #264: the sidebar partial must NOT introduce its own <form>.

    The desktop layout renders the sidebar inside a single outer
    ``library-browse-form``. Counting forms with html5lib (which honours the
    HTML5 rule that nested <form> start tags are ignored) is fragile, so we
    assert the partial source contains no <form> wrapper directly.
    """
    from pathlib import Path

    sidebar = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "idraa"
        / "templates"
        / "library"
        / "_filter_sidebar.html"
    )
    text = sidebar.read_text(encoding="utf-8")
    assert "<form" not in text, (
        "_filter_sidebar.html must not open its own <form>; it is always "
        "included inside library-browse-form (issue #264)"
    )


@pytest.mark.asyncio
async def test_viewer_can_browse_library_read_only(
    viewer_client: AsyncClient,
    seed_library_entry: Any,
) -> None:
    """Viewers can read /library (per spec §8.2 RBAC). They just can't wizard."""
    r = await viewer_client.get("/library")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_filter_sidebar_offers_ot_integrity_option(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """The browse filter sidebar must offer ``ot_integrity`` as a selectable
    threat-category checkbox when at least one published entry has that category.

    WS1 (data-driven facets): the sidebar now derives options from the actual
    published entries, not a hardcoded tuple list.  We seed an ot_integrity
    entry so the facet computation has a published entry to count.
    """
    integrity_entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="ot-integrity-sidebar-smoke",
        name="OT Integrity Sidebar Smoke",
        status="published",
        threat_event_type=ThreatCategory.OT_INTEGRITY,
        threat_actor_type=ThreatActorType.NATION_STATE,
        asset_class=AssetClass.OT_SYSTEMS,
        tags=[],
        description="OT integrity entry for sidebar facet test.",
        canonical_fair_gap="OT integrity sidebar FAIR gap test.",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 2.0, "high": 6.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        suggested_control_ids=[],
    )
    db_session.add(integrity_entry)
    await db_session.commit()

    r = await analyst_client.get("/library")
    assert r.status_code == 200
    assert 'name="threat_event_type"' in r.text
    assert 'value="ot_integrity"' in r.text


@pytest.mark.asyncio
async def test_get_library_filters_by_ot_integrity(
    analyst_client: AsyncClient,
    seed_library_entry: Any,
    db_session: AsyncSession,
) -> None:
    """Filtering ``threat_event_type=ot_integrity`` returns the integrity entry
    and excludes a non-integrity one. The ORM has no CHECK so ``ot_integrity``
    constructs fine here; the route validates the value against ThreatCategory."""
    integrity_entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="ot-integrity-filter-smoke",
        name="OT Integrity Filter Smoke",
        status="published",
        threat_event_type=ThreatCategory.OT_INTEGRITY,
        threat_actor_type=ThreatActorType.NATION_STATE,
        asset_class=AssetClass.OT_SYSTEMS,
        tags=[],
        description="Manipulation-of-view integrity entry for filter smoke.",
        canonical_fair_gap="OT integrity FAIR gap test.",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 2.0, "high": 6.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        suggested_control_ids=[],
    )
    db_session.add(integrity_entry)
    await db_session.commit()

    r = await analyst_client.get("/library?threat_event_type=ot_integrity")
    assert r.status_code == 200
    # The ot_integrity entry appears; the (availability/other) seed entry does not.
    assert integrity_entry.name in r.text
    assert seed_library_entry.name not in r.text


@pytest.mark.asyncio
async def test_library_entry_detail_shows_lognormal_distribution_type(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Epic B #326 Task 8 Step 3: a library entry whose primary_loss is native
    lognormal renders the shared ``pert_distribution_chart`` macro's lognormal
    branch on the detail page (label 'Lognormal'). No library-specific display
    logic — confirms Task 6's macro upgrade reaches the library entry page."""
    import math

    entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="lognormal-display-smoke",
        name="Lognormal Display Smoke",
        status="published",
        threat_event_type=ThreatCategory.MALWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="Native-lognormal primary-loss entry for display smoke test.",
        canonical_fair_gap="Lognormal display FAIR gap test.",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 2.0, "high": 6.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "lognormal",
            "mean": math.log(1_000_000.0),
            "sigma": 1.3,
        },
        suggested_control_ids=[],
    )
    db_session.add(entry)
    await db_session.commit()

    r = await analyst_client.get(f"/library/entries/{entry.id}")
    assert r.status_code == 200
    assert "Lognormal" in r.text


@pytest.mark.asyncio
async def test_entry_detail_shows_vendor_confidence_badge(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Epic C-i #335 Task 5: a library entry with loss_tier='vendor' renders
    a 'vendor-sourced — lower confidence' badge on the detail page."""
    import math

    entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="vendor-badge-smoke",
        name="Vendor Badge Smoke",
        status="published",
        threat_event_type=ThreatCategory.MALWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="Vendor-tier entry for confidence badge smoke test.",
        canonical_fair_gap="Vendor badge FAIR gap test.",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 2.0, "high": 6.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "lognormal",
            "mean": math.log(1_000_000.0),
            "sigma": 1.0,
        },
        suggested_control_ids=[],
        loss_tier="vendor",
    )
    db_session.add(entry)
    await db_session.commit()

    r = await analyst_client.get(f"/library/entries/{entry.id}")
    assert r.status_code == 200
    html_lower = r.text.lower()
    assert "vendor-sourced" in html_lower or "lower confidence" in html_lower, (
        "Expected a vendor-confidence badge in the entry detail page for loss_tier='vendor'"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("loss_tier", ["paginated", "anecdotal", "none"])
async def test_entry_detail_no_badge_for_non_vendor_tiers(
    loss_tier: str,
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Epic C-i #335 Task 5 (methodology I-1): library entries with loss_tier
    values other than 'vendor' must NOT render a vendor-confidence badge.

    Covers all three non-vendor tiers — paginated, anecdotal, and none — to
    guard the "TIER-2 confidence surfaced, never spuriously caveat TIER-1/3"
    hard rule.  The template's ``== "vendor"`` condition is correct; this test
    is the regression guard that catches any future drift (e.g. widening the
    condition to ``!= "paginated"`` would fail on anecdotal/none).
    """
    import math

    slug = f"no-badge-{loss_tier}-smoke"
    name = f"No Badge Smoke — {loss_tier}"
    entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug=slug,
        name=name,
        status="published",
        threat_event_type=ThreatCategory.MALWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description=f"{loss_tier}-tier entry — no confidence badge expected.",
        canonical_fair_gap=f"{loss_tier} no-badge FAIR gap test.",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 2.0, "high": 6.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "lognormal",
            "mean": math.log(1_000_000.0),
            "sigma": 1.0,
        },
        suggested_control_ids=[],
        loss_tier=loss_tier,
    )
    db_session.add(entry)
    await db_session.commit()

    r = await analyst_client.get(f"/library/entries/{entry.id}")
    assert r.status_code == 200
    assert "vendor-sourced" not in r.text.lower(), (
        f"Vendor-confidence badge must NOT appear for loss_tier='{loss_tier}'"
    )


@pytest.mark.asyncio
async def test_entry_detail_citations_linkify_https_sec_i1(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Sec-I1 (issue #349): the citations block linkifies https:// URLs and
    keeps hostile-scheme payloads as inert escaped text.

    Fixture has three citations:
      1. A real https URL — exactly one <a> must appear.
      2. javascript:alert(1) — must NOT appear in any href= attribute.
      3. <script> tag — must appear HTML-escaped, not executed.
    """
    entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="citation-linkify-sec-i1-smoke",
        name="Citation Linkify Sec-I1 Smoke",
        status="published",
        threat_event_type=ThreatCategory.MALWARE,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="Entry for citation linkify Sec-I1 regression test.",
        canonical_fair_gap="Citation linkify FAIR gap test.",
        source_citations=[
            "Cyentia IRIS 2025, https://example.test/iris.pdf (accessed 2026-06-10)",
            "EVIL, javascript:alert(1)",
            "<script>x</script> plain",
        ],
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 2.0, "high": 6.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        suggested_control_ids=[],
    )
    db_session.add(entry)
    await db_session.commit()

    r = await analyst_client.get(f"/library/entries/{entry.id}")
    assert r.status_code == 200

    # Isolate the citations region by finding text between "Source citations"
    # heading and the next closing </ul>.
    citations_start = r.text.find("Source citations")
    assert citations_start != -1, "Source citations heading not rendered"
    ul_start = r.text.find("<ul", citations_start)
    ul_end = r.text.find("</ul>", ul_start) + len("</ul>")
    citations_region = r.text[ul_start:ul_end]

    # Exactly one <a element in the citations block.
    assert citations_region.count("<a ") == 1, (
        f"Expected exactly 1 anchor in citations region, got "
        f"{citations_region.count('<a ')}:\n{citations_region}"
    )

    # The anchor href is the https URL with correct security attrs.
    assert 'href="https://example.test/iris.pdf"' in citations_region
    assert 'rel="noopener noreferrer"' in citations_region
    assert 'target="_blank"' in citations_region

    # javascript: must never appear as an href value.
    import re as _re

    assert not _re.search(r'href=["\']javascript:', citations_region), (
        "javascript: scheme must not appear as href in citations region"
    )

    # The <script> tag must be HTML-escaped, not raw.
    assert "&lt;script&gt;" in citations_region, "<script> tag in citation must be HTML-escaped"
    assert "<script>" not in citations_region


# ---------------------------------------------------------------------------
# WS1: data-driven facet integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sidebar_no_dead_end_filter_business_process_third_party_revenue(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """WS1 bug-class guard: a zero-coverage asset class must NOT appear in the
    sidebar as a filter option.

    ``business_process_third_party_revenue`` currently has 0 published library
    entries → it must be absent from the rendered sidebar.  We seed one
    ``ot_systems`` entry so the facet computation has something to return
    (otherwise the sidebar renders no checkboxes at all and the absence of
    BPTR would not be meaningful).
    """
    ot_entry = ScenarioLibraryEntry(
        id=uuid.uuid4(),
        version=1,
        slug="ot-ws1-bptr-guard",
        name="OT WS1 BPTR Guard",
        status="published",
        threat_event_type=ThreatCategory.OT_AVAILABILITY,
        threat_actor_type=ThreatActorType.NATION_STATE,
        asset_class=AssetClass.OT_SYSTEMS,
        tags=[],
        description="OT entry for WS1 dead-end filter guard.",
        canonical_fair_gap="WS1 guard FAIR gap.",
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 0.5, "mode": 2.0, "high": 6.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
        suggested_control_ids=[],
    )
    db_session.add(ot_entry)
    await db_session.commit()

    r = await analyst_client.get("/library")
    assert r.status_code == 200

    # ot_systems IS present → its filter checkbox must appear.
    assert 'value="ot_systems"' in r.text, (
        "ot_systems has a published entry but its filter checkbox is absent — "
        "data-driven facet is broken"
    )

    # business_process_third_party_revenue has 0 entries → must be absent.
    assert 'value="business_process_third_party_revenue"' not in r.text, (
        "business_process_third_party_revenue (0 published entries) appeared as "
        "a filter option — dead-end filter not eliminated"
    )


@pytest.mark.asyncio
async def test_sidebar_shows_facet_count(
    analyst_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """WS1: sidebar renders entry counts next to each facet label."""
    for i in range(3):
        db_session.add(
            ScenarioLibraryEntry(
                id=uuid.uuid4(),
                version=1,
                slug=f"count-smoke-{i}",
                name=f"Count Smoke {i}",
                status="published",
                threat_event_type=ThreatCategory.MALWARE,
                threat_actor_type=ThreatActorType.CYBERCRIMINALS,
                asset_class=AssetClass.DATA,
                tags=[],
                description=f"Count smoke entry {i}.",
                canonical_fair_gap="Count smoke gap.",
                source_citations=[],
                threat_event_frequency={
                    "distribution": "PERT",
                    "low": 1.0,
                    "mode": 4.0,
                    "high": 12.0,
                },
                vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
                primary_loss={
                    "distribution": "PERT",
                    "low": 100_000.0,
                    "mode": 750_000.0,
                    "high": 5_000_000.0,
                },
                suggested_control_ids=[],
            )
        )
    await db_session.commit()

    r = await analyst_client.get("/library")
    assert r.status_code == 200
    # The sidebar renders "Data (3)" or similar — the count must be there.
    assert "(3)" in r.text, "Facet count '(3)' not found in sidebar — WS1 count rendering broken"
