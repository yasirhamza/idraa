from __future__ import annotations

import re

import pytest
from sqlalchemy import delete

from idraa.models.threat_community import ThreatCommunity
from tests.routes.test_confirm_threat_community import _make
from tests.unit.test_threat_communities_service import org_row_from

_PROFILE_HEADINGS = (
    "Motive",
    "Primary intent",
    "Sponsorship",
    "Preferred general target characteristics",
    "Preferred targets",
    "Capability",
    "Personal risk tolerance",
    "Concern for collateral damage",
    "What counts as a threat event",
    "TEF landmark",
    "TCap landmark",
    "Reference organisation",
)


async def _check(client) -> None:
    r = await client.get("/library/threat-communities")
    assert r.status_code == 200 and r.text.count("/library/threat-communities/") >= 9
    assert re.search(r"\bpriors?\b", r.text, re.I) is None and "min–ML–max" not in r.text
    assert "5th · most likely · 95th" in r.text
    d = await client.get("/library/threat-communities/nation_state")
    assert d.status_code == 200
    for h in _PROFILE_HEADINGS:
        assert h in d.text, h
    assert (
        "landmark" in d.text.lower()
        and "never converted" in d.text.lower()
        and "not a range to tighten" in d.text.lower()
    )
    a = await client.get("/library/threat-communities/insider_accidental")
    assert (
        a.status_code == 200
        and "Not applicable" in a.text
        and "never converted" not in a.text.lower()
    )
    for page in (
        d,
        a,
    ):  # spec §5.1: the word "prior" appears on NEITHER page — the detail pages carry the free-text derivations
        assert re.search(r"\bpriors?\b", page.text, re.I) is None


async def test_pages_for_viewer(authed_viewer) -> None:
    await _check(authed_viewer[0])


async def test_pages_for_reviewer(authed_reviewer) -> None:
    await _check(authed_reviewer[0])


async def test_pages_for_analyst(authed_analyst) -> None:
    await _check(authed_analyst[0])


async def test_pages_for_admin(authed_admin) -> None:
    await _check(authed_admin[0])


async def test_list_marks_non_malicious_tcap_not_applicable(authed_viewer) -> None:
    client, _ = authed_viewer
    html = (await client.get("/library/threat-communities")).text
    assert html.count("Not applicable") >= 1


async def test_anonymous_redirects_to_login(client) -> None:
    client.cookies.clear()
    r = await client.get("/library/threat-communities", follow_redirects=False)
    assert r.status_code in (302, 303, 307)


async def test_detail_scenarios_are_org_scoped(
    authed_analyst, db_session, seed_organization_factory
) -> None:
    client, org_id = authed_analyst
    mine = await _make(db_session, org_id, slug="hacktivists", prov="assigned")
    mine.name = "MINE-ONLY"
    other = await seed_organization_factory(name="Other")
    theirs = await _make(db_session, other.id, slug="hacktivists", prov="assigned")
    theirs.name = "THEIRS-ONLY"
    await db_session.commit()
    html = (await client.get("/library/threat-communities/hacktivists")).text
    assert "MINE-ONLY" in html and "THEIRS-ONLY" not in html


async def test_malformed_and_unknown_slugs_404(authed_viewer) -> None:
    client, _ = authed_viewer
    for path in (
        "/library/threat-communities/martians",
        "/library/threat-communities/%2e%2e",
        "/library/threat-communities/Nation_State",
        "/library/threat-communities/" + "a" * 65,
    ):
        assert (await client.get(path)).status_code == 404, path


async def test_javascript_citation_url_renders_as_text(
    authed_viewer, db_session, seed_threat_communities
) -> None:
    """Sec-I5: the seed regex is a first gate; the render-time gate is the one that matters."""
    row = org_row_from(seed_threat_communities["competitors"], slug="competitors")
    row.id, row.version, row.source = seed_threat_communities["competitors"].id, 2, "seed"
    row.citations = [{"title": "evil", "url": "javascript:alert(1)"}]
    db_session.add(row)
    await db_session.commit()
    try:
        client, _ = authed_viewer
        html = (await client.get("/library/threat-communities/competitors")).text
        assert 'href="javascript:' not in html and "evil" in html
    finally:
        await db_session.execute(delete(ThreatCommunity).where(ThreatCommunity.version == 2))
        await db_session.commit()


async def test_detail_shows_conversion_caveat_for_a_converted_landmark(
    authed_viewer, seed_threat_communities
) -> None:
    converted = [
        s
        for s, r in seed_threat_communities.items()
        if r.tef_landmark["source_event_level"] != "attempt"
    ]
    if not converted:
        pytest.fail(
            "Task 1 must ship at least one converted TEF landmark (seed test pins this too)"
        )
    client, _ = authed_viewer
    html = (await client.get(f"/library/threat-communities/{converted[0]}")).text
    assert "do not use it as your scenario" in html
