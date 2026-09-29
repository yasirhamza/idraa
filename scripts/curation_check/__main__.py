"""CLI: python -m scripts.curation_check <all|labels|controls|overlap|gaps|tally> [...] (spec §5).

Live runs go through scripts/curation-check, which supplies the API key on stdin
(--key-stdin). The key is never read from the environment or an argument.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from scripts.curation_check.checks.gaps import load_intake
from scripts.curation_check.checks.overlap import merge_pairs
from scripts.curation_check.config import (
    DEFAULT_TOP,
    JEV_MODEL,
    MAX_ERROR_SHARE,
    REPO_ROOT,
    TOKEN_WARN_AT,
)
from scripts.curation_check.criteria import load_criteria
from scripts.curation_check.judge import Judge, JudgeFatalError, Recorder, question_hash
from scripts.curation_check.library import load_controls, load_scenarios, seed_hashes
from scripts.curation_check.report import queue_keys, render, tally
from scripts.curation_check.run import Job, build_jobs, run_check

CHECKS_FOR = {
    "labels": ["scenario-labels"],
    "controls": ["control-functions"],
    "overlap": ["overlap"],
    "gaps": ["gaps"],
    "all": ["scenario-labels", "control-functions", "overlap", "gaps"],
}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _git_commit(root: Path) -> str:
    try:
        out = _git(root, "rev-parse", "HEAD")
    except OSError:
        return "unknown"
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "unknown"


def _intake_is_safe(path: Path, root: Path) -> bool:
    """Intake files may hold licensed text: outside the repo, or inside it only when git ignores them."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    try:
        return _git(root, "check-ignore", "-q", str(path.resolve())).returncode == 0
    except OSError:
        return False


def _make_judge(args: argparse.Namespace, jobs: dict[str, list[Job]], api_key: str | None) -> Judge:
    if args.judge == "replay":
        from scripts.curation_check.judges.replay import ReplayJudge

        try:
            judge = ReplayJudge(args.replay)
        except (OSError, ValueError, KeyError) as e:  # missing, unreadable or corrupt recording
            raise JudgeFatalError(
                f"replay file not readable: {args.replay} ({type(e).__name__})"
            ) from e
        expected = {
            j.item_key: question_hash(j.state, j.questions) for js in jobs.values() for j in js
        }
        stale = judge.stale_items(expected)
        if stale:
            raise JudgeFatalError(
                f"recording is stale for {len(stale)} item{'s' if len(stale) != 1 else ''} "
                f"(questions or seed data changed): {', '.join(stale[:10])}"
            )
        return judge
    # Task 8 creates this module; until then mypy sees it as an untyped/missing import.
    from scripts.curation_check.judges.jev import JevJudge  # type: ignore[import-untyped]

    if not api_key:
        raise JudgeFatalError("live runs need the key on stdin; use scripts/curation-check")
    return JevJudge.from_sdk(JEV_MODEL, api_key=api_key)  # type: ignore[no-any-return]


def _tally(root: Path) -> int:
    paths = sorted((root / "docs" / "curation").glob("*/report.md"))
    if not paths:
        print("No committed reports under docs/curation/.")
        return 0
    try:
        counts = tally(paths)
    except ValueError as e:  # an edited report the parser cannot attribute
        print(f"curation-check: {e}", file=sys.stderr)
        return 3
    print(
        "Hit rate = accepted / decided, with 90% Wilson intervals (approximate: one curator's calls are not independent)."
    )
    for (model, check, top), c in sorted(counts.items()):
        lo, hi = c["hit_interval"]
        print(
            f"{model} · {check} · top {top}: accepted {c['accepted']}, rejected {c['rejected']}, deferred {c['deferred']}, "
            f"blank {c['blank']} | hit {c['accepted']}/{c['decided']} (90% {lo:.0%}-{hi:.0%}) | {c['verdict']}"
        )
    return 0


def main(argv: list[str] | None = None, stdin: TextIO | None = None) -> int:
    p = argparse.ArgumentParser(prog="curation-check")
    p.add_argument("command", choices=[*CHECKS_FOR, "tally"])
    p.add_argument("--judge", choices=["jev", "replay"], default="jev")
    p.add_argument("--replay", type=Path)
    p.add_argument("--top", type=int, default=DEFAULT_TOP)
    p.add_argument("--campaign", default="adhoc")
    p.add_argument("--intake", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--compare-to", type=Path)
    p.add_argument(
        "--force", action="store_true", help="overwrite an existing report in the output folder"
    )
    p.add_argument("--key-stdin", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    a = p.parse_args(argv)

    if a.command == "tally":
        return _tally(a.root)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", a.campaign):
        p.error("--campaign must be lowercase letters, digits and hyphens")
    if a.top < 1:
        p.error("--top must be at least 1")
    if a.judge == "replay" and a.replay is None:
        p.error("--judge replay needs --replay PATH")
    if a.judge == "jev" and a.replay is not None:
        p.error("--replay is only for --judge replay")
    previous = None  # checked before any judge call, so a bad path never wastes a paid run
    if a.compare_to is not None:
        try:
            previous = json.loads((a.compare_to / "run.json").read_text(encoding="utf-8"))["queues"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            p.error(
                f"--compare-to {a.compare_to}: no readable run.json with queues ({type(e).__name__})"
            )
    api_key = None
    if a.judge == "jev":
        if a.key_stdin:
            api_key = (stdin or sys.stdin).readline().strip()
        if not api_key:
            p.error("live runs go through scripts/curation-check, which supplies the key")
    checks = list(CHECKS_FOR[a.command])
    notes: list[str] = []
    if "gaps" in checks and a.intake is None:
        if a.command == "gaps":
            p.error("gaps needs --intake PATH")
        checks.remove("gaps")
        notes.append("gaps skipped: no --intake")
    if "gaps" in checks and not _intake_is_safe(a.intake, a.root):
        p.error(
            "--intake must be outside the repo, or ignored by git (intake files are never committed)"
        )

    date = datetime.now(UTC).date().isoformat()
    out: Path = a.out or a.root / "curation-runs" / f"{date}-{a.campaign}"
    if (out / "report.md").exists() and not a.force:
        p.error(
            f"{out} already has a report (it may hold dispositions); pass --force or choose another --out"
        )
    if a.replay is not None and (out / "responses.jsonl").resolve() == a.replay.resolve():
        p.error(
            "--out must differ from the recording's folder (the recording would be overwritten)"
        )

    criteria = load_criteria()
    scenarios, controls = load_scenarios(a.root), load_controls(a.root)
    try:
        intake = load_intake(a.intake) if "gaps" in checks else []
    except ValueError as e:
        p.error(str(e))
    jobs = {
        c: build_jobs(c, scenarios=scenarios, controls=controls, criteria=criteria, intake=intake)
        for c in checks
    }

    judge: Judge | None = None
    try:
        judge = _make_judge(a, jobs, api_key)
        del api_key  # drop our reference; the SDK client keeps its own copy for the run
        recorder = Recorder(out / "responses.jsonl")
        results = {c: run_check(c, jobs[c], judge, recorder) for c in checks}
    except JudgeFatalError as e:
        print(f"curation-check: {e}", file=sys.stderr)
        return 3
    finally:
        close = getattr(judge, "close", None)
        if callable(close):
            close()
    if "overlap" in results:
        results["overlap"].flags = merge_pairs(results["overlap"].flags)

    failed = [c for c, r in results.items() if r.error_share > MAX_ERROR_SHARE]
    warnings = notes + [
        f"{c}: input tokens reached {max(r.input_tokens)} (warning at {TOKEN_WARN_AT})"
        for c, r in results.items()
        if r.input_tokens and max(r.input_tokens) >= TOKEN_WARN_AT
    ]
    models = sorted(set().union(*(r.models for r in results.values())))
    meta = {
        "campaign": a.campaign,
        "date": date,
        "judge": judge.name,
        "model": ", ".join(models) or "n/a",
        "git_commit": _git_commit(a.root),
    }
    checks_meta: dict[str, Any] = {
        c: {
            "items": r.items,
            "candidates": len(r.flags) - r.suppressed,
            "suppressed": r.suppressed,
            "errored": len(r.errored),
            "errored_items": [k for k, _ in r.errored],
            "input_tokens_max": max(r.input_tokens, default=None),
            "input_tokens_median": float(statistics.median(r.input_tokens))
            if r.input_tokens
            else None,
        }
        for c, r in results.items()
    }
    run = {
        **meta,
        "top": a.top,
        "seed_sha256": seed_hashes(a.root),
        "criteria_sha256": criteria.sha256,
        "checks": checks_meta,
        "failed_checks": failed,
        "warnings": warnings,
        "queues": queue_keys(results, a.top),
    }
    (out / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render(meta, results, a.top, previous), encoding="utf-8")

    for c, r in results.items():
        print(
            f"{c}: {r.items} items, {len(r.flags) - r.suppressed} candidates, {len(r.errored)} errored"
        )
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"report: {out / 'report.md'}")
    if failed:
        print(
            f"curation-check: more than {MAX_ERROR_SHARE:.0%} of items errored in {failed}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
