"""Scenario label audit (spec §4.1) and control function audit (spec §4.2).
Scores only order the review queue; they are not calibrated probabilities."""

from __future__ import annotations

from collections.abc import Mapping

from scripts.curation_check.criteria import NONE_FITS, TAXONOMY_FIELDS
from scripts.curation_check.flags import Flag
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
        # Task-4 review: a tie never claims a preference over the curated label, nor for 'not enough information'
        top = max(dist, key=lambda k: (dist[k], k == curated, k != NONE_FITS, k))
        named = [k for k in dist if k not in (curated, NONE_FITS)]
        alt = max(named, key=lambda k: (dist[k], k)) if named else None
        nxt = f"; next: **{alt}** {dist[alt]:.2f}" if alt is not None else ""
        if top == NONE_FITS:
            finding = f"judge's top score is 'not enough information' ({dist[top]:.2f}); curated **{curated}** score {p:.2f}{nxt}"
        elif top == curated:
            finding = f"judge's top score agrees with curated **{curated}** ({p:.2f}){nxt}"
        elif top in _NO_NAMED_TYPE and curated not in _NO_NAMED_TYPE:
            finding = f"judge's top score is **{top}** ({dist[top]:.2f}), meaning no named type fits; curated **{curated}** score {p:.2f}"
        else:
            finding = (
                f"curated **{curated}** score {p:.2f}; judge prefers **{top}** ({dist[top]:.2f})"
            )
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
