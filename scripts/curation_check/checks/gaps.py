"""Coverage gaps: intake threats no library scenario covers (spec §4.4).
Intake files are never committed; only an item's id, source and a title capped at
INTAKE_TITLE_MAX characters reach a committed report."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scripts.curation_check.config import INTAKE_TITLE_MAX
from scripts.curation_check.criteria import NONE_COVERED
from scripts.curation_check.flags import Flag
from scripts.curation_check.judge import Answers

INTAKE_SOURCE_MAX = 40  # the source is published in the queue row, like the title
GAP_QUESTION = "Which library scenario covers the threat described in the state?"
NONE_COVERED_TEXT = "No library scenario covers this threat."
COVERS_DEFINITION = (
    "A library scenario covers the threat when its threat, method and effect would model the loss event the "
    "item describes (the FAIR scenario scope). The asset may be broader than, or unstated in, the item."
)
_FIELDS = ("id", "title", "text", "source")


@dataclass(frozen=True)
class IntakeItem:
    id: str
    title: str
    text: str
    source: str

    def state(self) -> dict[str, str]:
        return {"threat_item_title": self.title, "threat_item_text": self.text}


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def load_intake(path: Path) -> list[IntakeItem]:
    items: list[IntakeItem] = []
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{path}: not readable ({type(e).__name__})") from e
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: line {n}: not valid JSON ({e.msg})") from e
        for f in _FIELDS:
            if not isinstance(row.get(f), str) or not row[f].strip():
                raise ValueError(f"{path}: line {n}: missing or empty '{f}'")
        clean = {f: " ".join(row[f].split()) for f in _FIELDS}
        if clean["id"] in seen:
            raise ValueError(f"{path}: line {n}: duplicate id {clean['id']!r}")
        seen.add(clean["id"])
        items.append(
            IntakeItem(
                clean["id"],
                _cap(clean["title"], INTAKE_TITLE_MAX),
                clean["text"],
                _cap(clean["source"], INTAKE_SOURCE_MAX),
            )
        )
    return items


def score_gap(item: IntakeItem, answers: Answers, *, name_to_slug: dict[str, str]) -> list[Flag]:
    dist = answers["match"]
    if not isinstance(dist, Mapping):
        raise TypeError("match: expected a choice distribution")
    p_none = float(dist.get(NONE_COVERED, 0.0))
    ranked = sorted(
        ((k, float(v)) for k, v in dist.items() if k != NONE_COVERED and k in name_to_slug),
        key=lambda t: (-t[1], t[0]),
    )
    closest_p = ranked[0][1] if ranked else 0.0
    tied = [k for k, p in ranked if p == closest_p]  # Task-5 review: a tie never names one entry
    closest = tied[0] if tied else "(none)"
    label = "closest (tied)" if len(tied) > 1 else "closest"
    names = ", ".join(f"**{k}**" for k in tied) or "**(none)**"
    return [
        Flag(
            check="gaps",
            key=f"gaps:{item.id}",
            subject=f"{item.id} · {item.title} ({item.source})",
            score=p_none,
            finding=f"judge's none-score {p_none:.2f}; {label}: {names} ({closest_p:.2f})",
            detail={
                "closest": closest,
                "closest_p": closest_p,
                "closest_tied": tied,
                "top2": [[k, p] for k, p in ranked[:2]],
                "source": item.source,
            },
        )
    ]
