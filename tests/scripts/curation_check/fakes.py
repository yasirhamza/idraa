"""Deterministic fake judge for offline tests."""

from __future__ import annotations

import hashlib
from typing import Any

from scripts.curation_check.judge import JudgeResult


class FakeJudge:
    name = "fake"

    def __init__(self, broken: frozenset[str] = frozenset()) -> None:
        self.broken = broken  # item keys whose answers are deliberately malformed

    def ask(self, item_key: str, state: dict[str, Any], questions: dict[str, Any]) -> JudgeResult:
        answers: dict[str, Any] = {}
        for qid, q in questions.items():
            h = int(hashlib.sha256(f"{item_key}|{qid}".encode()).hexdigest(), 16)
            if q["type"] == "noul":
                answers[qid] = (h % 101) / 100
                continue
            keys = sorted(q["criteria"])
            pick = keys[h % len(keys)]
            rest = 0.3 / (len(keys) - 1) if len(keys) > 1 else 0.0
            answers[qid] = (
                {k: (0.7 if k == pick else rest) for k in keys} if len(keys) > 1 else {pick: 1.0}
            )
        if item_key in self.broken:
            answers.pop(next(iter(answers)))
        return JudgeResult(answers=answers, input_tokens=1000, model="fake-1", ms=1.0)
