"""Scenario label audit (spec §4.1) and control function audit (spec §4.2).
Scores only order the review queue; they are not calibrated probabilities."""

from __future__ import annotations

from collections.abc import Mapping

from scripts.curation_check.criteria import NONE_FITS, TAXONOMY_FIELDS
from scripts.curation_check.flags import Flag, name_list, tied_with
from scripts.curation_check.judge import Answers
from scripts.curation_check.library import ControlItem, ScenarioItem

_NO_NAMED_TYPE = frozenset({"miscellaneous", "other"})


def score_scenario(item: ScenarioItem, answers: Answers) -> list[Flag]:
    flags = []
    for field in TAXONOMY_FIELDS:
        dist = answers[field]
        if not isinstance(dist, Mapping):
            raise TypeError(f"{field}: expected a choice distribution")
        curated = getattr(item, field)
        p = float(dist.get(curated, 0.0))
        # Task-4 and PR-gate review: ties are judged at the displayed precision, like overlap and gaps;
        # a tie never claims a preference over the curated label, nor for 'not enough information'
        ranked = sorted(((k, float(v)) for k, v in dist.items()), key=lambda t: (-t[1], t[0]))
        top_p = ranked[0][1]
        tied = tied_with(ranked, top_p)
        named_tied = [k for k in tied if k not in (curated, NONE_FITS)]
        named = [k for k, _ in ranked if k not in (curated, NONE_FITS)]
        alt = named[0] if named else None
        nxt = f"; next: **{alt}** {dist[alt]:.2f}" if alt is not None else ""
        if curated in tied:
            also_tied = [name_list(named_tied)] if named_tied else []
            if NONE_FITS in tied:
                also_tied.append("'not enough information'")
            tie_note = f", tied with {' and '.join(also_tied)}" if also_tied else nxt
            finding = f"judge's top score agrees with curated **{curated}** ({p:.2f}){tie_note}"
        elif not named_tied:
            finding = f"judge's top score is 'not enough information' ({top_p:.2f}); curated **{curated}** score {p:.2f}{nxt}"
        elif (
            len(named_tied) == 1
            and named_tied[0] in _NO_NAMED_TYPE
            and curated not in _NO_NAMED_TYPE
        ):
            finding = (
                f"judge's top score is **{named_tied[0]}** ({top_p:.2f}), meaning no named type fits; "
                f"curated **{curated}** score {p:.2f}"
            )
        elif len(named_tied) == 1:
            finding = f"curated **{curated}** score {p:.2f}; judge prefers **{named_tied[0]}** ({top_p:.2f})"
        else:
            finding = f"judge's top scores tie: {name_list(named_tied)} ({top_p:.2f}); curated **{curated}** score {p:.2f}"
        flags.append(
            Flag(
                check="scenario-labels",
                key=f"scenario-labels:{item.slug}:{field}",
                subject=f"{item.slug} · {field}",
                score=1.0 - p,
                finding=finding,
            )
        )
    return flags


def score_control(item: ControlItem, answers: Answers, *, labels: dict[str, str]) -> list[Flag]:
    flags = []
    for slug in sorted(labels):
        value = answers["fn:" + slug]
        if isinstance(value, Mapping):
            raise TypeError(f"fn:{slug}: expected a yes/no score")
        p = float(value)
        label = labels[slug]
        detail: dict[str, object]
        if slug in item.functions:
            score = 1.0 - p
            finding = f"labelled **{label}**; judge's yes-score {p:.2f}"
            detail = {"direction": "wrong"}
        else:
            score = p
            finding = f"not labelled **{label}**; judge's yes-score {p:.2f}"
            detail = {"direction": "missing"}
            if slug in item.dropped:
                detail.update(suppressed=True, reason=item.dropped[slug])
        flags.append(
            Flag(
                check="control-functions",
                key=f"control-functions:{item.slug}:{slug}",
                subject=f"{item.slug} · {label}",
                score=score,
                finding=finding,
                detail=detail,
            )
        )
    return flags
