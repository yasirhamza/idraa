"""Scenario overlap: which library entries are hard to tell apart (spec §4.3).
Score = 1 - the judge's 'distinct' score, so a cluster of similar entries doesn't dilute it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

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
_ACTION = ": merge, sharpen, or keep"


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
    # Task-5 review: a tie never picks one partner, and the copy never claims a match 'distinct' outscored
    top_p = others[0][1]
    tied = [
        n for n, p in others if round(p, 2) == round(top_p, 2)
    ]  # a tie at the displayed precision
    rest = [(n, p) for n, p in others if n not in tied]
    names = ", ".join(f"**{n}**" for n in tied)
    tie = ", tied" if len(tied) > 1 else ""
    if round(p_distinct, 2) >= round(top_p, 2):
        lead = f"from {item.slug}: judge's top score is 'distinct' ({p_distinct:.2f}); closest: {names} ({top_p:.2f}{tie})"
    else:
        lead = f"from {item.slug}: judge's top score is {names} ({top_p:.2f}{tie}); distinct-score {p_distinct:.2f}"
    also = f"; also **{rest[0][0]}** ({rest[0][1]:.2f})" if rest else ""
    flags = []
    for name in tied:
        a, b = sorted((item.slug, name_to_slug[name]))
        flags.append(
            Flag(
                check="overlap",
                key=f"overlap:{a}:{b}",
                subject=f"{a} ↔ {b}",
                score=1.0 - p_distinct,
                finding=f"{lead}{also}{_ACTION}",
                detail={
                    "top2": [[n, p] for n, p in others[:2]],
                    "from": item.slug,
                    "pair_p": top_p,
                },
            )
        )
    return flags


def merge_pairs(flags: list[Flag]) -> list[Flag]:
    """Keep the stronger direction of each pair; carry the other direction's score so its evidence stays visible."""
    groups: dict[str, list[Flag]] = {}
    for f in flags:
        groups.setdefault(f.key, []).append(f)
    merged = []
    for group in groups.values():
        group.sort(key=lambda f: (-f.score, f.detail.get("from", "")))
        keep = group[0]
        reverse = [f for f in group[1:] if f.detail.get("from") != keep.detail.get("from")]
        if reverse:
            r = reverse[0]
            keep = replace(
                keep,
                finding=(
                    keep.finding.removesuffix(_ACTION)
                    + f"; reverse: {r.detail['from']}'s score for this pair {r.detail['pair_p']:.2f}{_ACTION}"
                ),
                detail={**keep.detail, "reverse_p": r.detail["pair_p"]},
            )
        merged.append(keep)
    return merged
