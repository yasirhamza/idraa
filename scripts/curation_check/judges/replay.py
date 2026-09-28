"""Replays recorded judge responses: offline tests, re-scoring, reviewing old runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.curation_check.criteria import Question
from scripts.curation_check.judge import JudgeError, JudgeFatalError, JudgeResult, question_hash


class ReplayJudge:
    name = "replay"

    def __init__(self, path: Path) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                self._records[row["item_key"]] = row

    def stale_items(self, expected: dict[str, str]) -> list[str]:
        """Item keys whose recorded question hash differs from the current one."""
        return sorted(
            k for k, h in expected.items() if k in self._records and self._records[k]["qhash"] != h
        )

    def ask(
        self, item_key: str, state: dict[str, Any], questions: dict[str, Question]
    ) -> JudgeResult:
        rec = self._records.get(item_key)
        if rec is None:
            raise JudgeError(f"no recording for {item_key}")
        if rec["qhash"] != question_hash(state, questions):
            raise JudgeFatalError(
                f"stale recording for {item_key}: questions or seed data changed since it was recorded"
            )
        if "error" in rec:
            raise JudgeError(rec["error"])
        return JudgeResult(
            answers=rec["answers"],
            input_tokens=rec.get("input_tokens"),
            model=rec.get("model"),
            ms=rec.get("ms", 0.0),
        )
