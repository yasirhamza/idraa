"""Coverage gaps: intake threats no library scenario covers (spec §4.4).
Intake files are never committed; only an item's id, source and a title capped at
INTAKE_TITLE_MAX characters reach a committed report."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scripts.curation_check.config import INTAKE_TITLE_MAX, MATCH_NAME_MIN, MAX_NAMED
from scripts.curation_check.criteria import NONE_COVERED
from scripts.curation_check.flags import Flag, name_list, tied_with
from scripts.curation_check.judge import Answers

INTAKE_SOURCE_MAX = 40  # the source is published in the queue row, like the title
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

GAP_QUESTION = "Which library scenario covers the threat described in the state?"
NONE_COVERED_TEXT = "No library scenario covers this threat."
COVERS_DEFINITION = (
    "A library scenario covers the threat when its threat, method and effect would model the loss event the "
    "item describes (the FAIR scenario scope). The asset or effect may be broader than, or unstated in, the item; "
    "when the item states no effect, a scenario whose threat and method match covers it."
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
        if not _ID_RE.fullmatch(clean["id"]):
            raise ValueError(f"{path}: line {n}: id must be 1-64 letters, digits or . _ : -")
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
    if closest_p < MATCH_NAME_MIN:
        tied: list[str] = []
        named = f"closest: none scored {MATCH_NAME_MIN:.2f} or more"
    else:
        tied = tied_with(ranked, closest_p)  # Task-5 review: a tie never names one entry
        named = f"{'closest (tied)' if len(tied) > 1 else 'closest'}: {name_list(tied)} ({closest_p:.2f})"
    return [
        Flag(
            check="gaps",
            key=f"gaps:{item.id}",
            subject=f"{item.id} · {item.title} ({item.source})",
            score=p_none,
            finding=f"judge's none-score {p_none:.2f}; {named}",
            detail={
                "closest": tied[0] if tied else "(none)",
                "closest_p": closest_p,
                "closest_tied": tied[:MAX_NAMED],
                "closest_tied_more": max(0, len(tied) - MAX_NAMED),
                "top2": [[k, p] for k, p in ranked[:2]],
                "source": item.source,
            },
        )
    ]
