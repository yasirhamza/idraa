"""Scenario overlap: which library entries are hard to tell apart (spec §4.3).
Score = 1 - the judge's 'distinct' score, so a cluster of similar entries doesn't dilute it."""

from __future__ import annotations

from collections.abc import Mapping

from scripts.curation_check.criteria import NONE_DISTINCT
from scripts.curation_check.flags import Flag
from scripts.curation_check.judge import Answers
from scripts.curation_check.library import ScenarioItem

OVERLAP_QUESTION = (
    "Which other library scenario describes the same risk as the scenario in the state?"
)
SAME_RISK_DEFINITION = (
    "Two scenarios describe the same risk when they share the threat, the asset at risk, the method and the "
    "effect (the FAIR scenario scope). Differences only in sector, organisation size or calibration do not make "
    "entries distinct in scope; the curator decides whether such a variant is justified."
)
NONE_DISTINCT_TEXT = "No other scenario describes the same risk; this one is distinct."


def score_overlap(
    item: ScenarioItem, answers: Answers, *, name_to_slug: dict[str, str]
) -> list[Flag]:
    dist = answers["match"]
    if not isinstance(dist, Mapping):
        raise TypeError("match: expected a choice distribution")
    p_distinct = float(dist.get(NONE_DISTINCT, 0.0))
    others = sorted(
        ((name, float(p)) for name, p in dist.items() if name != NONE_DISTINCT),
        key=lambda t: (-t[1], t[0]),
    )
    if not others:
        return []
    top_name, top_p = others[0]
    a, b = sorted((item.slug, name_to_slug[top_name]))
    also = f"; also **{others[1][0]}** ({others[1][1]:.2f})" if len(others) > 1 else ""
    return [
        Flag(
            check="overlap",
            key=f"overlap:{a}:{b}",
            subject=f"{a} ↔ {b}",
            score=1.0 - p_distinct,
            finding=(
                f"**{item.name}** reads like **{top_name}** ({top_p:.2f}){also}; judge's distinct-score "
                f"{p_distinct:.2f}: merge, or sharpen the descriptions"
            ),
            detail={"top2": [[n, p] for n, p in others[:2]]},
        )
    ]


def merge_pairs(flags: list[Flag]) -> list[Flag]:
    best: dict[str, Flag] = {}
    for f in flags:
        if f.key not in best or f.score > best[f.key].score:
            best[f.key] = f
    return list(best.values())
