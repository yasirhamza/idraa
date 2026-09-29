"""Load the reviewed option wording and build System One question sets (spec §3, §4)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.curation_check.config import REPO_ROOT

CRITERIA_PATH = REPO_ROOT / "data" / "curation" / "criteria.json"
TAXONOMY_FIELDS = ("threat_event_type", "asset_class", "threat_actor_type")
NONE_FITS = "not enough information to decide"
NONE_FITS_TEXT = "The description does not give enough information to decide."
NONE_DISTINCT = "none: this scenario is distinct"
NONE_COVERED = "none of these"

Question = dict[str, Any]

_FIELD_QUESTION = {
    "threat_event_type": "What type of threat event does this library scenario describe?",
    "asset_class": "What class of asset is primarily at risk in this library scenario?",
    "threat_actor_type": "What type of threat actor carries out this library scenario?",
}


@dataclass(frozen=True)
class Criteria:
    version: int
    convention: str
    field_conventions: dict[str, str]
    subfunctions: dict[str, dict[str, Any]]
    taxonomy: dict[str, dict[str, dict[str, str]]]
    sha256: str


def load_criteria(path: Path = CRITERIA_PATH) -> Criteria:
    raw = path.read_bytes()
    data = json.loads(raw)
    return Criteria(
        version=int(data["version"]),
        convention=data["convention"],
        field_conventions=dict(data.get("field_conventions", {})),
        subfunctions=data["subfunctions"],
        taxonomy=data["taxonomy"],
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def scenario_label_questions(c: Criteria) -> dict[str, Question]:
    questions: dict[str, Question] = {}
    for field in TAXONOMY_FIELDS:
        instructions: dict[str, str] = {"question": _FIELD_QUESTION[field]}
        if field in c.field_conventions:
            instructions["convention"] = c.field_conventions[field]
        questions[field] = {
            "type": "choice",
            "instructions": instructions,
            "criteria": {**c.taxonomy[field], NONE_FITS: NONE_FITS_TEXT},
        }
    return questions


def control_function_questions(c: Criteria) -> dict[str, Question]:
    return {
        "fn:" + slug: {
            "type": "noul",
            "instructions": {
                "question": (
                    "Does the security control described in the state perform the "
                    f"FAIR-CAM function '{s['label']}'?"
                ),
                "convention": c.convention,
            },
            "criteria": {
                "true": f"{s['what']} Examples: {'; '.join(s['examples'])}.",
                "false": f"The control does not perform {s['label']}. {s['not_for']}",
            },
        }
        for slug, s in c.subfunctions.items()
    }


def library_match_question(
    options: dict[str, str],
    question: str,
    none_label: str,
    none_text: str,
    definition: str | None = None,
) -> Question:
    instructions: dict[str, str] = {"question": question}
    if definition:
        instructions["definition"] = definition
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": {**options, none_label: none_text},
    }
