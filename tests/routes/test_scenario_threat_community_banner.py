from __future__ import annotations

import re

from tests.conftest import csrf_post
from tests.routes.test_confirm_threat_community import _audit_count, _make


async def test_view_banner_and_confirm_form_for_analyst(authed_analyst, db_session) -> None:
    client, org_id = authed_analyst
    s = await _make(db_session, org_id)  # privileged_insider / migrated_split_default
    html = (await client.get(f"/scenarios/{s.id}")).text
    assert (
        "Threat community needs review" in html
        and f"/scenarios/{s.id}/confirm-threat-community" in html
    )
    assert "higher-capability" not in html and "conservative" not in html


async def test_edit_form_shows_notice_not_nested_form(authed_analyst, db_session) -> None:
    client, org_id = authed_analyst
    s = await _make(db_session, org_id, slug=None, prov="unassigned")
    html = (await client.get(f"/scenarios/{s.id}/edit")).text
    assert "Threat community needs review" in html and "confirm-threat-community" not in html


async def test_new_form_lists_grouped_communities_with_blank_first(authed_analyst) -> None:
    client, _ = authed_analyst
    html = (await client.get("/scenarios/new")).text
    assert '<optgroup label="Internal">' in html and '<optgroup label="External">' in html
    assert 'name="threat_community"' in html and "threat_actor_type" not in html
    assert html.index('<option value="" selected>— select —</option>') < html.index(
        '<optgroup label="Internal">'
    )


async def test_create_rejects_blank_community(authed_analyst) -> None:
    from tests.routes.test_scenario_form_attack_mappings import _scenario_form_payload

    client, _ = authed_analyst
    r = await csrf_post(client, "/scenarios", _scenario_form_payload(threat_community=""))
    assert r.status_code == 422 and "Choose a threat community" in r.text


async def test_edit_rejects_blank_community(authed_analyst, db_session) -> None:
    from tests.integration.test_draft_workflow import _valid_update_payload_for

    client, org_id = authed_analyst
    s = await _make(db_session, org_id, slug="hacktivists", prov="assigned")
    r = await csrf_post(
        client, f"/scenarios/{s.id}", _valid_update_payload_for(s) | {"threat_community": ""}
    )
    assert r.status_code == 422
    await db_session.refresh(s)
    assert s.threat_community.slug == "hacktivists" and s.row_version == 1


async def test_wizard_step2_rejects_blank_and_unknown_community(authed_analyst) -> None:
    """The step-2 POST resolves tx itself (routes/scenarios.py:2503-2512); bootstrap like
    tests/integration/_wizard_step3_test_helpers.py::_bootstrap_wizard_through_step_2 (POST step 1 with skip_library=1)."""
    client, _ = authed_analyst
    await csrf_post(client, "/scenarios/new/wizard/step/1", {"skip_library": "1"})
    for bad in ("", "martians", "a" * 65):
        r = await csrf_post(
            client,
            "/scenarios/new/wizard/step/2",
            {
                "name": "W",
                "threat_category": "malware",
                "threat_community": bad,
                "asset_class": "systems",
            },
        )
        assert r.status_code == 422 and "Choose a threat community" in r.text


async def test_wizard_reestimate_of_flagged_scenario_seeds_blank_community(
    authed_analyst, db_session
) -> None:
    """Sec4-I1: the wizard cannot attest the placeholder — step 2 starts blank for a flagged scenario.
    The entry point is POST /scenarios/{id}/re-estimate (routes/scenarios.py:1856; analyst+, 303 -> step 2)."""
    client, org_id = authed_analyst
    s = await _make(db_session, org_id)  # privileged_insider / migrated_split_default
    r = await csrf_post(client, f"/scenarios/{s.id}/re-estimate", {})
    assert r.status_code == 303
    html = (await client.get(r.headers["location"])).text
    assert re.search(
        r'name="threat_community"[^>]*>\s*<option value="" selected>', html
    )  # anchored to THIS select: the macro emits the same blank option for any empty select
    await db_session.refresh(s)
    assert s.threat_community_provenance == "migrated_split_default"


async def _latest_draft(db_session, org_id):
    """The idiom tests already use (tests/integration/_wizard_step3_test_helpers.py:68-74)."""
    from sqlalchemy import select

    from idraa.models.wizard_draft import WizardDraft

    return (
        await db_session.execute(
            select(WizardDraft)
            .where(WizardDraft.organization_id == org_id)
            .order_by(WizardDraft.updated_at.desc())
            .limit(1)
        )
    ).scalar_one()


async def _resume_step2_with_legacy_value(client, db_session, org_id, legacy: str) -> str:
    """Seed a PRE-P1-shaped draft: no `threat_community` key at all (the step-1 POST writes
    `threat_community: None`, which would short-circuit the legacy branch), plus the legacy key.
    Reassign state_json as a new dict (plain JSON column)."""
    await csrf_post(client, "/scenarios/new/wizard/step/1", {"skip_library": "1"})
    row = await _latest_draft(db_session, org_id)
    row.state_json = {k: v for k, v in row.state_json.items() if k != "threat_community"} | {
        "threat_actor_type": legacy
    }
    await db_session.commit()
    return (await client.get(f"/scenarios/new/wizard/step/2?tx={row.tx_id}")).text


async def test_legacy_draft_with_insider_malicious_resumes_blank(
    authed_analyst, db_session
) -> None:
    """A pre-P1 draft holding insider_malicious drops the key (no silent placeholder)…"""
    client, org_id = authed_analyst
    html = await _resume_step2_with_legacy_value(client, db_session, org_id, "insider_malicious")
    assert (
        re.search(r'name="threat_community"[^>]*>\s*<option value="" selected>', html)
        and 'value="privileged_insider" selected' not in html
    )


async def test_legacy_draft_with_one_to_one_value_resumes_mapped(
    authed_analyst, db_session
) -> None:
    """…while a 1:1 legacy value IS mapped — the positive control proving the legacy branch runs."""
    client, org_id = authed_analyst
    html = await _resume_step2_with_legacy_value(client, db_session, org_id, "hacktivists")
    assert 'value="hacktivists" selected' in html


async def test_finalize_refuses_draft_without_community(authed_analyst, db_session) -> None:
    """The backstop: a POST that skips step 2 on a flagged re-estimate cannot attest or unassign.
    Valid SME rows are seeded so that, were the community check missing or mis-ordered, the
    draft WOULD finalize — i.e. the refusal is what actually fires, not the SME-shape guard."""
    client, org_id = authed_analyst
    s = await _make(db_session, org_id)
    r = await csrf_post(client, f"/scenarios/{s.id}/re-estimate", {})
    tx = r.headers["location"].split("tx=")[1]
    row = await _latest_draft(db_session, org_id)
    row.state_json = {
        **row.state_json,
        "sme_estimates": {  # satisfies SMEEstimateRow (schemas/wizard_step3.py:33-50)
            "tef": [{"sme_name": "A", "low": 1.0, "high": 3.0}],
            "vuln": [{"sme_name": "A", "low": 0.1, "high": 0.3}],
            "pl": [{"sme_name": "A", "low": 1000.0, "high": 5000.0}],
        },
    }
    await db_session.commit()
    token_before = row.version_token
    n = await _audit_count(db_session)
    r2 = await csrf_post(
        client, f"/scenarios/new/wizard/finalize?tx={tx}", {"version_token": token_before}
    )
    assert r2.status_code == 422 and "Choose a threat community on step 2 before saving." in r2.text
    await db_session.refresh(s)
    await db_session.refresh(row)
    assert s.threat_community_provenance == "migrated_split_default" and s.row_version == 1
    assert row.version_token == token_before and await _audit_count(db_session) == n


async def test_edit_form_roundtrip_keeps_community(authed_analyst, db_session) -> None:
    """Task 6 review carry-in: GET the edit form for an ASSIGNED scenario, re-post its own
    field values UNCHANGED, and assert the save is a true no-op for the community (slug +
    provenance + row_version unchanged, no new audit row) — closing the interim bug where
    form.html posted name="threat_actor_type" while the parser read "threat_community",
    silently clearing the community on every edit-form save."""
    from bs4 import BeautifulSoup

    client, org_id = authed_analyst
    s = await _make(db_session, org_id, slug="hacktivists", prov="assigned")
    html = (await client.get(f"/scenarios/{s.id}/edit")).text
    soup = BeautifulSoup(html, "html5lib")
    form = soup.find("form", attrs={"action": f"/scenarios/{s.id}"})
    assert form is not None, "edit form (action=/scenarios/{id}) not found on the edit page"

    payload: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name or name == "_csrf":
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("checkbox", "radio"):
            if inp.has_attr("checked"):
                payload[name] = inp.get("value", "on")
            continue
        payload[name] = inp.get("value", "") or ""
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        payload[name] = opt.get("value", "") if opt is not None else ""
    for ta in form.find_all("textarea"):
        name = ta.get("name")
        if name:
            payload[name] = ta.text or ""

    assert payload.get("threat_community") == "hacktivists"

    n_before = await _audit_count(db_session)
    r = await csrf_post(client, f"/scenarios/{s.id}", payload)
    assert r.status_code == 303, r.text
    await db_session.refresh(s)
    assert s.threat_community is not None and s.threat_community.slug == "hacktivists"
    assert s.threat_community_provenance == "assigned"
    assert s.row_version == 1
    assert await _audit_count(db_session) == n_before
