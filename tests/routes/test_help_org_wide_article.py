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
async def test_article_attribution_limits_match_the_code(
    authed_analyst: tuple[AsyncClient, object],
) -> None:
    """Section 4 quotes the limits users actually hit. Each figure is read from the
    code constant or default it describes, so a retuned limit fails here instead of
    leaving the article stale."""
    from idraa.config import Settings
    from idraa.services.run_executor import (
        MAX_ATTRIBUTION_CONTROLS,
        MAX_ATTRIBUTION_TOTAL_EVALS,
        _scenario_eval_cost,
    )

    client, _ = authed_analyst
    r = await client.get(f"/help/{_SLUG}", headers={"HX-Request": "true"})
    assert r.status_code == 200
    text = " ".join(r.text.split())
    fields = Settings.model_fields
    for figure in (
        f"more than {MAX_ATTRIBUTION_CONTROLS} controls",
        f"budget of {MAX_ATTRIBUTION_TOTAL_EVALS:,} calculations",
        f"up to {fields['weight_ensemble_draws'].default} draws",
        f"budget of {fields['weight_ensemble_eval_budget'].default:,} calculations",
        f"Below {fields['weight_ensemble_min_draws'].default} the check is not run",
        # scenarios that fit the run-wide budget at 12 and 13 controls (rounded in the text)
        f"about {round(MAX_ATTRIBUTION_TOTAL_EVALS / _scenario_eval_cost(12), -1):.0f} scenarios",
        f"about {MAX_ATTRIBUTION_TOTAL_EVALS // _scenario_eval_cost(13)}.",
    ):
        assert figure in text, figure
    # "about 4,600 per control" is the Maleki sample count (4,612) per control
    assert _scenario_eval_cost(13) // 13 == 4612

    # The three ensemble examples. Draw count K under the cache-credited cost model:
    # draw 1 costs ceil(realized * (1 + r)), each later draw ceil(realized * r), within
    # the band's eval budget (the same arithmetic as services/run_executor.py).
    import math

    from idraa.services import weight_robustness

    budget = fields["weight_ensemble_eval_budget"].default

    def draws(players: int, scenarios: int) -> int:
        realized = scenarios * _scenario_eval_cost(players)
        assert realized <= MAX_ATTRIBUTION_TOTAL_EVALS  # nothing skipped by the run budget
        r = weight_robustness.FINALIZE_TO_COMPOSE_COST_RATIO
        first = math.ceil(realized * (1.0 + r))
        per = math.ceil(realized * r)
        return int(min(fields["weight_ensemble_draws"].default, 1 + (budget - first) // per))

    min_draws = fields["weight_ensemble_min_draws"].default
    assert (draws(13, 10), draws(20, 10), draws(12, 400)) == (34, 21, 11)
    assert draws(13, 10) >= min_draws  # 34 >= 32: ran, not skipped
    assert draws(20, 10) < min_draws and draws(12, 400) < min_draws  # skipped
    for example in (
        "could afford 34 draws, so the check ran",
        "could afford 21",
        "could afford 11, so it was skipped",
        "only three band-endpoint draws are evaluated",
        "too few simulation draws",
        "a later, smaller scenario may still fit",
    ):
        assert example in text, example


@pytest.mark.asyncio
async def test_article_rerun_paragraph_states_who_marks_runs(
    authed_analyst: tuple[AsyncClient, object],
) -> None:
    """The "Re-runs" paragraph says what marks runs, and it must stay true in both directions.
    An in-effect organization-wide control marks every completed run in the organization; a
    control that is not in effect does not trigger THAT marking, but a library re-sync of any
    adopted control still marks the runs that used it (apply_resync), and ordinary edits mark
    nothing. "Only ..." and "mark nothing" for every edit were false and are pinned out."""
    client, _ = authed_analyst
    r = await client.get(f"/help/{_SLUG}", headers={"HX-Request": "true"})
    assert r.status_code == 200
    text = " ".join(r.text.split())
    for sentence in (
        "A change to an organization-wide control that is in effect (flagged organization-wide, "
        "published, implementation stage Active) marks every completed analysis in the "
        "organization as needing a re-run.",
        "do not trigger that organization-wide marking: a library re-sync of any adopted control "
        "still marks the completed runs that used it",
    ):
        assert sentence in text, sentence
    assert "Only a change to an organization-wide control" not in text
    assert "change no scenario and mark nothing" not in text


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
