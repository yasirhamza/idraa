"""Answer validation, recording and replay (spec §3, §7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.curation_check.judge import (
    JudgeError,
    JudgeFatalError,
    JudgeResult,
    Recorder,
    question_hash,
    validate_answers,
)
from scripts.curation_check.judges.replay import ReplayJudge

QUESTIONS: dict[str, Any] = {
    "pick": {"type": "choice", "instructions": {"question": "?"}, "criteria": {"a": "A", "b": "B"}},
    "yes": {"type": "noul", "instructions": {"question": "?"}},
}
STATE = {"text": "hello"}


def test_valid_answers_pass() -> None:
    validate_answers(QUESTIONS, {"pick": {"a": 0.7, "b": 0.3}, "yes": 0.4})


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ({"pick": {"a": 1.0}}, "missing"),
        ({"pick": {"a": 0.5, "zzz": 0.5}, "yes": 0.1}, "unknown options"),
        ({"pick": {"a": 0.5, "b": 0.2}, "yes": 0.1}, "sum to 1"),
        ({"pick": {"a": 1.0}, "yes": 1.5}, r"\[0, 1\]"),
        ({"pick": {"a": 1.0}, "yes": True}, r"\[0, 1\]"),
        ({"pick": 0.3, "yes": 0.1}, "map options"),
    ],
)
def test_invalid_answers_raise_item_errors(answers: dict[str, Any], message: str) -> None:
    with pytest.raises(JudgeError, match=message):
        validate_answers(QUESTIONS, answers)


def test_question_hash_is_stable_and_sensitive() -> None:
    assert question_hash(STATE, QUESTIONS) == question_hash(dict(STATE), dict(QUESTIONS))
    assert question_hash({"text": "other"}, QUESTIONS) != question_hash(STATE, QUESTIONS)


def _record_one(tmp_path: Path) -> Path:
    path = tmp_path / "responses.jsonl"
    rec = Recorder(path)
    ok = JudgeResult(
        answers={"pick": {"a": 0.7, "b": 0.3}, "yes": 0.4}, input_tokens=120, model="m-1", ms=5.0
    )
    rec.record("scenario-labels", "scenario-labels:x", question_hash(STATE, QUESTIONS), ok, None)
    rec.record(
        "scenario-labels", "scenario-labels:y", question_hash(STATE, QUESTIONS), None, "boom"
    )
    return path


def test_recorder_writes_one_json_line_per_item(tmp_path: Path) -> None:
    rows = [json.loads(line) for line in _record_one(tmp_path).read_text().splitlines()]
    assert [r["item_key"] for r in rows] == ["scenario-labels:x", "scenario-labels:y"]
    assert rows[0]["answers"]["yes"] == 0.4 and rows[0]["model"] == "m-1"
    assert rows[1]["error"] == "boom"


def test_replay_returns_recorded_answers(tmp_path: Path) -> None:
    judge = ReplayJudge(_record_one(tmp_path))
    result = judge.ask("scenario-labels:x", STATE, QUESTIONS)
    assert result.answers["pick"] == {"a": 0.7, "b": 0.3}
    assert result.input_tokens == 120


def test_replay_reraises_recorded_errors_and_missing_items(tmp_path: Path) -> None:
    judge = ReplayJudge(_record_one(tmp_path))
    with pytest.raises(JudgeError, match="boom"):
        judge.ask("scenario-labels:y", STATE, QUESTIONS)
    with pytest.raises(JudgeError, match="no recording"):
        judge.ask("scenario-labels:z", STATE, QUESTIONS)


def test_replay_refuses_stale_recordings(tmp_path: Path) -> None:
    judge = ReplayJudge(_record_one(tmp_path))
    changed = {"text": "changed"}
    assert judge.stale_items({"scenario-labels:x": question_hash(changed, QUESTIONS)}) == [
        "scenario-labels:x"
    ]
    assert judge.stale_items({"scenario-labels:x": question_hash(STATE, QUESTIONS)}) == []
    with pytest.raises(JudgeFatalError, match="stale"):
        judge.ask("scenario-labels:x", changed, QUESTIONS)
