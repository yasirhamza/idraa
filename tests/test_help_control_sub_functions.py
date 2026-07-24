"""New control-sub-functions help article + form trigger (#395).

Slug renamed control-sub-functions -> how-controls-change-the-numbers
(help-overhaul P1 T1); the old slug now 301s (tested in
tests/routes/test_help_redirects.py).
"""

import pytest

from idraa.help_content import HELP_BY_SLUG, help_url


def test_article_registered():
    assert "how-controls-change-the-numbers" in HELP_BY_SLUG
    a = HELP_BY_SLUG["how-controls-change-the-numbers"]
    assert a.title == "FAIR-CAM sub-functions"  # Meth-I2: title pin unchanged
    # cross-link wiring is bidirectional with controls-overlays
    assert "controls-overlays" in a.related
    assert "how-controls-change-the-numbers" in HELP_BY_SLUG["controls-overlays"].related


def test_help_url_resolves():
    assert help_url("how-controls-change-the-numbers") == "/help/how-controls-change-the-numbers"


@pytest.mark.asyncio
async def test_article_renders(authed_analyst):
    client, _org_id = authed_analyst
    resp = await client.get("/help/how-controls-change-the-numbers")
    assert resp.status_code == 200
    for fam in ("Loss Event Control", "Variance Management", "Decision Support"):
        assert fam in resp.text
    # old slug 301s (full-page); redirect-path behavior has its own tests.
    old = await client.get("/help/control-sub-functions", follow_redirects=False)
    assert old.status_code == 301


@pytest.mark.asyncio
async def test_form_has_help_trigger(authed_analyst):
    client, _org_id = authed_analyst
    resp = await client.get("/controls/new")
    assert "/help/how-controls-change-the-numbers" in resp.text
