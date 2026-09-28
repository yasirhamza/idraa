"""Build per-item jobs for each check and run them through a judge (spec §4, §7)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from scripts.curation_check.checks.gaps import (
    COVERS_DEFINITION,
    GAP_QUESTION,
    NONE_COVERED_TEXT,
    IntakeItem,
    score_gap,
)
from scripts.curation_check.checks.labels import score_control, score_scenario
from scripts.curation_check.checks.overlap import (
    NONE_DISTINCT_TEXT,
    OVERLAP_QUESTION,
    SAME_RISK_DEFINITION,
    score_overlap,
)
from scripts.curation_check.criteria import (
    NONE_COVERED,
    NONE_DISTINCT,
    Criteria,
    Question,
    control_function_questions,
    library_match_question,
    scenario_label_questions,
)
from scripts.curation_check.flags import CheckResult, Flag
from scripts.curation_check.judge import (
    Answers,
    Judge,
    JudgeError,
    Recorder,
    question_hash,
    validate_answers,
)
from scripts.curation_check.library import ControlItem, ScenarioItem, match_options


@dataclass(frozen=True)
class Job:
    item_key: str
    state: dict[str, Any]
    questions: dict[str, Question]
    score: Callable[[Answers], list[Flag]]


def build_jobs(
    check: str,
    *,
    scenarios: list[ScenarioItem],
    controls: list[ControlItem],
    criteria: Criteria,
    intake: list[IntakeItem],
) -> list[Job]:
    if check == "scenario-labels":
        qs = scenario_label_questions(criteria)
        return [
            Job(f"{check}:{s.slug}", s.state(), qs, partial(score_scenario, s)) for s in scenarios
        ]
    if check == "control-functions":
        qs = control_function_questions(criteria)
        labels = {slug: s["label"] for slug, s in criteria.subfunctions.items()}
        return [
            Job(f"{check}:{c.slug}", c.state(), qs, partial(score_control, c, labels=labels))
            for c in controls
        ]
    if check == "overlap":
        jobs = []
        for s in scenarios:
            options, name_to_slug = match_options(scenarios, exclude_slug=s.slug)
            q = {
                "match": library_match_question(
                    options,
                    OVERLAP_QUESTION,
                    NONE_DISTINCT,
                    NONE_DISTINCT_TEXT,
                    SAME_RISK_DEFINITION,
                )
            }
            jobs.append(
                Job(
                    f"{check}:{s.slug}",
                    s.state(),
                    q,
                    partial(score_overlap, s, name_to_slug=name_to_slug),
                )
            )
        return jobs
    if check == "gaps":
        options, name_to_slug = match_options(scenarios)
        q = {
            "match": library_match_question(
                options, GAP_QUESTION, NONE_COVERED, NONE_COVERED_TEXT, COVERS_DEFINITION
            )
        }
        return [
            Job(f"{check}:{i.id}", i.state(), q, partial(score_gap, i, name_to_slug=name_to_slug))
            for i in intake
        ]
    raise ValueError(f"unknown check {check!r}")


def run_check(check: str, jobs: list[Job], judge: Judge, recorder: Recorder) -> CheckResult:
    """Judge every job; per-item failures are recorded and skipped. JudgeFatalError propagates."""
    res = CheckResult(check=check, items=len(jobs))
    for job in jobs:
        qhash = question_hash(job.state, job.questions)
        try:
            result = judge.ask(job.item_key, job.state, job.questions)
            validate_answers(job.questions, result.answers)
        except JudgeError as e:
            res.errored.append((job.item_key, str(e)))
            recorder.record(check, job.item_key, qhash, None, str(e))
            continue
        recorder.record(check, job.item_key, qhash, result, None)
        if result.input_tokens is not None:
            res.input_tokens.append(result.input_tokens)
        if result.model:
            res.models.add(result.model)
        res.flags.extend(job.score(result.answers))
    return res
