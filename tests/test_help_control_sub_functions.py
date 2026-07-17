"""New control-sub-functions help article + form trigger (#395)."""

import pytest

from idraa.help_content import HELP_BY_SLUG, help_url


def test_article_registered():
    assert "control-sub-functions" in HELP_BY_SLUG
    a = HELP_BY_SLUG["control-sub-functions"]
    assert a.title == "FAIR-CAM sub-functions"
    # cross-link wiring is bidirectional with controls-overlays
    assert "controls-overlays" in a.related
    assert "control-sub-functions" in HELP_BY_SLUG["controls-overlays"].related


def test_help_url_resolves():
    assert help_url("control-sub-functions") == "/help/control-sub-functions"


@pytest.mark.asyncio
async def test_article_renders(authed_analyst):
    client, _org_id = authed_analyst
    resp = await client.get("/help/control-sub-functions")
    assert resp.status_code == 200
    for fam in ("Loss Event Control", "Variance Management", "Decision Support"):
        assert fam in resp.text


@pytest.mark.asyncio
async def test_form_has_help_trigger(authed_analyst):
    client, _org_id = authed_analyst
    resp = await client.get("/controls/new")
    assert "/help/control-sub-functions" in resp.text
