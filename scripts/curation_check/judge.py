"""The pluggable judge interface, answer validation and response recording (spec §3, §7)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from scripts.curation_check.criteria import Question

Answer = Mapping[str, float] | float
Answers = Mapping[str, Answer]


@dataclass(frozen=True)
class JudgeResult:
    answers: Answers
    input_tokens: int | None
    model: str | None
    ms: float


class JudgeError(Exception):
    """One item failed; the run records it and continues."""


class JudgeFatalError(Exception):
    """The run must stop (bad key, stale recording, missing SDK)."""


class Judge(Protocol):
    name: str

    def ask(
        self, item_key: str, state: dict[str, Any], questions: dict[str, Question]
    ) -> JudgeResult: ...


def question_hash(state: dict[str, Any], questions: dict[str, Question]) -> str:
    payload = json.dumps(
        {"state": state, "questions": questions}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_answers(questions: dict[str, Question], answers: Answers) -> None:
    missing = sorted(set(questions) - set(answers))
    if missing:
        raise JudgeError(f"answers missing for {missing[:3]}")
    for qid, q in questions.items():
        a = answers[qid]
        if q["type"] == "noul":
            if isinstance(a, bool) or not isinstance(a, int | float) or not 0.0 <= float(a) <= 1.0:
                raise JudgeError(f"{qid}: yes/no answer must be a number in [0, 1]")
            continue
        if not isinstance(a, Mapping):
            raise JudgeError(f"{qid}: choice answer must map options to probabilities")
        unknown = sorted(set(a) - set(q["criteria"]))
        if unknown:
            raise JudgeError(
                f"{qid}: {len(unknown)} unknown option(s), e.g. {[k[:40] for k in unknown[:3]]}"
            )
        if any(isinstance(v, bool) or not isinstance(v, int | float) for v in a.values()):
            raise JudgeError(f"{qid}: probabilities must be numbers in [0, 1] that sum to 1")
        if any(not 0.0 <= float(v) <= 1.0 for v in a.values()) or abs(sum(a.values()) - 1.0) > 0.02:
            raise JudgeError(f"{qid}: probabilities must be in [0, 1] and sum to 1")


class Recorder:
    """Appends one JSON line per judged item; the file is the replay input for later runs.
    It stores answers and a hash of the question and state, never the state text itself."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def record(
        self, check: str, item_key: str, qhash: str, result: JudgeResult | None, error: str | None
    ) -> None:
        row: dict[str, Any] = {"check": check, "item_key": item_key, "qhash": qhash}
        if error is not None or result is None:
            row["error"] = error or "no result"
        else:
            row.update(
                answers=dict(result.answers),
                input_tokens=result.input_tokens,
                model=result.model,
                ms=round(result.ms, 1),
            )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
