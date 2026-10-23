"""Guard: the CI wiring of the ``--no-dev`` runtime smoke and the merge aggregator (#260, #158).

``scripts/runtime_smoke.py`` only protects anything if the ``runtime-smoke`` job
(a) really installs without dev extras and runs the script without letting ``uv``
sync them back, (b) is one of ``ci-success``'s ``needs`` (the only required check
that aggregates the deterministic core), and (c) never turns off the script's
own dev-environment refusal with ``--allow-dev-env``. None of these fails any
other test if a later edit loosens it, so this pins them against the parsed
``ci.yml``. ``dast`` and the other core jobs are pinned in ``ci-success.needs`` too:
dropping one silently un-gates merges.

If you are changing ``ci.yml`` on purpose, change this test in the same PR and
get it reviewed as a gate-integrity change.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_CI = _WORKFLOWS / "ci.yml"

_REQUIRED_NEEDS = frozenset({"gate", "secrets", "sast", "dast", "runtime-smoke"})
_SYNC = "uv sync --frozen --no-dev"
_SMOKE_SCRIPT = "scripts/runtime_smoke.py"
_BYPASS_FLAG = "--allow-dev-env"


def _needs(job: dict[str, Any]) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in job.get("steps", []) if "run" in step]


def wiring_problems(workflow: dict[str, Any]) -> list[str]:
    """Human-readable wiring problems in a parsed ci.yml (empty list = wired correctly)."""
    problems: list[str] = []
    jobs = workflow.get("jobs", {})

    missing = _REQUIRED_NEEDS - _needs(jobs.get("ci-success", {}))
    if missing:
        problems.append(f"ci-success.needs is missing {sorted(missing)}")

    smoke = jobs.get("runtime-smoke")
    if smoke is None:
        problems.append("job runtime-smoke is missing")
        return problems

    runs = _runs(smoke)
    sync_at = [i for i, r in enumerate(runs) if _SYNC in r]
    # The invocation: a run that names the script AND passes uv's --no-sync, so `uv run`
    # cannot re-sync (and re-add) the dev extras before running it.
    invoke_at = [i for i, r in enumerate(runs) if _SMOKE_SCRIPT in r and "--no-sync" in r]
    if not sync_at:
        problems.append(f"runtime-smoke has no step running `{_SYNC}`")
    if not invoke_at:
        problems.append(f"runtime-smoke does not run {_SMOKE_SCRIPT} with `uv run --no-sync`")
    if sync_at and invoke_at and min(sync_at) > min(invoke_at):
        problems.append("runtime-smoke runs the smoke script before the --no-dev sync")
    problems.extend(f"runtime-smoke step re-adds dev extras: {r!r}" for r in runs if "--extra" in r)
    return problems


def uses_bypass_flag(workflow: Path) -> bool:
    """True if the workflow text contains the smoke script's dev-env bypass flag anywhere."""
    return _BYPASS_FLAG in workflow.read_text(encoding="utf-8")


def _ci() -> dict[str, Any]:
    loaded = yaml.safe_load(_CI.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _workflow_files() -> list[Path]:
    return sorted([*_WORKFLOWS.glob("*.yml"), *_WORKFLOWS.glob("*.yaml")])


# --- the real workflows -------------------------------------------------------


def test_ci_yml_wiring_is_intact() -> None:
    assert wiring_problems(_ci()) == []


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_no_workflow_bypasses_the_dev_environment_refusal(workflow: Path) -> None:
    assert not uses_bypass_flag(workflow), f"{workflow.name} passes {_BYPASS_FLAG}"


def test_the_guard_actually_sees_the_workflows() -> None:
    # Vacuity guard: an empty glob must not make the per-workflow check pass silently.
    assert "ci.yml" in {p.name for p in _workflow_files()}


# --- negative self-tests on a synthetic workflow -------------------------------


def _good() -> dict[str, Any]:
    return {
        "jobs": {
            "ci-success": {"needs": ["gate", "secrets", "sast", "dast", "runtime-smoke"]},
            "runtime-smoke": {
                "steps": [
                    {"uses": "actions/checkout@x"},
                    {"run": "uv sync --frozen --no-dev"},
                    {"run": "uv run --no-sync python scripts/runtime_smoke.py"},
                ]
            },
        }
    }


def test_synthetic_good_workflow_is_clean() -> None:
    assert wiring_problems(_good()) == []


@pytest.mark.parametrize("dropped", sorted(_REQUIRED_NEEDS))
def test_dropping_a_needs_entry_is_flagged(dropped: str) -> None:
    wf = _good()
    wf["jobs"]["ci-success"]["needs"].remove(dropped)
    assert any(dropped in p and "ci-success.needs" in p for p in wiring_problems(wf))


def test_needs_given_as_a_bare_string_is_not_a_loophole() -> None:
    wf = _good()
    wf["jobs"]["ci-success"]["needs"] = "gate"
    assert any("ci-success.needs" in p for p in wiring_problems(wf))


def test_a_missing_runtime_smoke_job_is_flagged() -> None:
    wf = _good()
    del wf["jobs"]["runtime-smoke"]
    assert "job runtime-smoke is missing" in wiring_problems(wf)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda steps: steps.__setitem__(1, {"run": "uv sync --frozen"}),
            "no step running `uv sync --frozen --no-dev`",
        ),
        (
            lambda steps: steps.__setitem__(2, {"run": "uv run python scripts/runtime_smoke.py"}),
            "`uv run --no-sync`",
        ),
        (
            lambda steps: steps.__setitem__(1, {"run": "uv sync --frozen --no-dev --extra dev"}),
            "re-adds dev extras",
        ),
        (
            lambda steps: steps.reverse(),
            "before the --no-dev sync",
        ),
    ],
    ids=["sync-without-no-dev", "run-without-no-sync", "extra-dev", "wrong-order"],
)
def test_loosening_the_runtime_smoke_job_is_flagged(mutate: Any, expected: str) -> None:
    wf = copy.deepcopy(_good())
    mutate(wf["jobs"]["runtime-smoke"]["steps"])
    assert any(expected in p for p in wiring_problems(wf)), wiring_problems(wf)


def test_bypass_flag_detector_flags_a_synthetic_workflow(tmp_path: Path) -> None:
    flagged = tmp_path / "bad.yml"
    flagged.write_text(
        "run: uv run --no-sync python scripts/runtime_smoke.py --allow-dev-env\n", encoding="utf-8"
    )
    clean = tmp_path / "ok.yml"
    clean.write_text("run: uv run --no-sync python scripts/runtime_smoke.py\n", encoding="utf-8")
    assert uses_bypass_flag(flagged)
    assert not uses_bypass_flag(clean)
