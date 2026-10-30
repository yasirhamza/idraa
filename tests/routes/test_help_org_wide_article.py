"""Help article `organization-wide-controls` (issue #299, Task 11).

Registry placement + content pins. The needles are the load-bearing wording: the
Variance Management / Decision Support pairing rules, the absent-from-meta-uplift
reliability convention (register C7), the no-standalone-reduction reading, and the basis
sentence that stops a reader checking fair share against the Monte-Carlo headline.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from idraa.help_content import HELP_ARTICLES, HELP_BY_SLUG

_SLUG = "organization-wide-controls"


def test_article_registered_once_in_guide_track() -> None:
    hits = [a for a in HELP_ARTICLES if a.slug == _SLUG]
    assert len(hits) == 1 and hits[0].track == "guide"
    orders = [a.order for a in HELP_ARTICLES if a.track == "guide"]
    assert len(orders) == len(set(orders)), "guide-track order values must stay unique"


def test_article_related_slugs_resolve() -> None:
    related = HELP_BY_SLUG[_SLUG].related
    assert related == ("how-controls-change-the-numbers", "controls-overlays")
    assert all(slug in HELP_BY_SLUG for slug in related)


@pytest.mark.asyncio
async def test_article_renders(authed_analyst: tuple[AsyncClient, object]) -> None:
    client, _ = authed_analyst
    r = await client.get(f"/help/{_SLUG}", headers={"HX-Request": "true"})
    assert r.status_code == 200
    for needle in (
        "Variance Management",
        "Decision Support",
        "explicitly",
        "absent",
        "no standalone reduction",
        "Correcting Misaligned Decisions",
        "unavailable",
        # The section-4 basis sentence (spec D8): the only guard that the
        # "sum of fair shares = the closed-form modelled reduction, not the Monte-Carlo
        # headline" wording survives later article edits.
        "typical-case",
        "will not match the headline",
        # Section 2: the VMC pair rule is scoped to Variance Management, and Treatment
        # Selection stays label-only (register C8).
        "score as a pair",
        "label-only",
    ):
        assert needle in r.text, needle


@pytest.mark.asyncio
async def test_article_does_not_carry_the_retired_pairing_wording(
    authed_analyst: tuple[AsyncClient, object],
) -> None:
    """An Identification-only (or Implementation-only) variant is NOT unconditionally
    zero: the Variance Management pair forms across all co-present controls, so it
    contributes where another control supplies the other half (register C8). The
    unscoped "contributes 0 until an Implementation function exists" is false."""
    client, _ = authed_analyst
    r = await client.get(f"/help/{_SLUG}", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "contributes 0 until" not in r.text
    assert "Identification and Implementation score as a pair" not in r.text


@pytest.mark.asyncio
async def test_departures_page_mirrors_register_c7a(
    authed_analyst: tuple[AsyncClient, object],
) -> None:
    """The departures page declares itself the plain-English mirror of the register; the
    control-model section must carry the C7a applicability default (issue #299) so the two
    cannot drift silently, and it must point readers at the new article."""
    client, _ = authed_analyst
    r = await client.get("/help/where-idraa-departs-from-fair", headers={"HX-Request": "true"})
    assert r.status_code == 200
    for needle in (
        "applicability default",
        "organization-wide",
        "supersedes the inherited copy",
        "at most 0.1",
        'href="/help/organization-wide-controls"',
    ):
        assert needle in r.text, needle
