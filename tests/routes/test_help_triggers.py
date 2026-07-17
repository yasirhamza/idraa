"""Each documented feature page exposes a help_trigger to the right article."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,slug",
    [
        ("/analyses/new", "run-and-read-analyses"),
        ("/library", "libraries"),
        ("/controls", "controls-overlays"),
        ("/overlays", "controls-overlays"),
    ],
)
async def test_feature_page_has_help_trigger(authed_analyst, url, slug):
    client, _ = authed_analyst
    r = await client.get(url)
    assert r.status_code == 200
    assert f'hx-get="/help/{slug}"' in r.text


@pytest.mark.asyncio
async def test_reports_page_has_help_trigger(authed_analyst):
    """Reports list (/reports) renders the 'reports' help trigger for analysts."""
    client, _ = authed_analyst
    r = await client.get("/reports")
    assert r.status_code == 200
    assert 'hx-get="/help/reports"' in r.text


@pytest.mark.asyncio
async def test_scenario_import_page_has_help_trigger(authed_admin):
    """Scenario import (/scenarios/import) renders the 'import-export' trigger.

    The import route is ADMIN-only (require_role(UserRole.ADMIN)) so we use
    the authed_admin fixture.
    """
    client, _ = authed_admin
    r = await client.get("/scenarios/import")
    assert r.status_code == 200
    assert 'hx-get="/help/import-export"' in r.text


@pytest.mark.asyncio
async def test_wizard_shell_has_help_trigger(analyst_client):
    """Wizard step 1 (/scenarios/new/wizard) renders the 'build-a-scenario' trigger.

    The trigger lives in the shared wizard shell (_shell.html) so it appears
    on every wizard step.  Step 1 is reachable via a plain GET.
    """
    r = await analyst_client.get("/scenarios/new/wizard")
    assert r.status_code == 200
    assert 'hx-get="/help/build-a-scenario"' in r.text
