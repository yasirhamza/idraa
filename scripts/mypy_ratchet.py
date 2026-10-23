#!/usr/bin/env python3
"""mypy ratchet for the test tree (#28) — the error count may fall, never rise.

Production code (``src/idraa``, ``fair_cam`` source, ``security``) is held to
zero mypy errors by the "mypy" step of ``scripts/run_local_gate.py``. The test
tree (``tests`` and ``fair_cam/tests``) is not: it carries ~2,000 historical
errors, and a clean-up was ruled out (owner, 2026-10-23). This script is the
alternative — a RATCHET against ``scripts/mypy_tests_baseline.json``:

* a file whose error count exceeds its baseline FAILS;
* a file absent from the baseline that has any error FAILS;
* a grown ``_total`` FAILS;
* otherwise PASS. A file that dropped below its baseline prints a one-line note
  suggesting ``--write-baseline`` so the ratchet tightens (never auto-rewritten).

``--write-baseline`` regenerates the file. It refuses (exit 1) when ANY file's count
would rise above its committed value, when a file absent from the committed baseline
has any error (a rename counts), or when the new ``_total`` is higher, unless
``--allow-increase`` is passed — so the ratchet only loosens on purpose (state the
reason in the commit). A committed baseline that cannot be read is replaced only with
``--allow-increase`` (or by deleting the file first).

Exit codes (fail closed, same convention as ``scripts/sca_gate.py``):
0 = PASS, 1 = FAIL (or a refused baseline increase), 2 = tool error (mypy not
installed or crashed, output that does not parse, a corrupt baseline, or a baseline
whose recorded ``_generated_with`` flags differ from the ones this script runs mypy with).

mypy runs with ``--platform linux --python-version 3.11`` so the count is the same on a
macOS dev machine and on the Linux CI runner; the baseline records those flags. It also
keeps its OWN cache dir (``.mypy_cache/ratchet``): the gate's "mypy" step runs under the
host platform, and two option sets sharing one cache invalidate each other on every run.

Stdlib only; mypy itself runs via ``sys.executable -m mypy`` so the venv that
runs the gate is the venv that runs the tool.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PureWindowsPath

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "mypy_tests_baseline.json"

TEST_TREES: tuple[str, ...] = ("tests", str(PurePath("fair_cam") / "tests"))
# mypy's own flags: type-check as if on this platform/version whatever the host is, so a
# macOS dev machine and the Linux CI runner count identically (sys.platform branches and
# platform-conditional stubs would otherwise shift per-file counts between them).
PLATFORM_FLAGS: tuple[str, ...] = ("--platform", "linux", "--python-version", "3.11")
PLATFORM_FLAGS_TEXT = " ".join(PLATFORM_FLAGS)
# A cache dir of its own (under the gitignored .mypy_cache/): the gate's "mypy" step runs with
# the host platform and mypy keys cache entries on the options, so sharing one dir made the two
# steps rewrite each other's entries on every macOS push (A1, ~10 s each way).
CACHE_DIR = str(PurePath(".mypy_cache") / "ratchet")
MYPY_ARGV: tuple[str, ...] = (
    "-m",
    "mypy",
    "--config-file=pyproject.toml",
    *PLATFORM_FLAGS,
    f"--cache-dir={CACHE_DIR}",
    *TEST_TREES,
    "--no-error-summary",
    "--show-error-codes",
)

TOTAL_KEY = "_total"
GENERATED_WITH_KEY = "_generated_with"
RESERVED_KEYS = frozenset({TOTAL_KEY, GENERATED_WITH_KEY})
WRITE_HINT = "python -m scripts.mypy_ratchet --write-baseline"
REPLACE_HINT = (
    f"replace it with `{WRITE_HINT} --allow-increase`, or delete the file and run `{WRITE_HINT}`"
)

# `path:line:col: error: msg`, `path:line: error: msg` or `path: error: msg`.
# Non-greedy path so a Windows drive letter ("C:\\x.py") backtracks correctly.
_DIAG_RE = re.compile(
    r"^(?P<path>.+?):(?:\d+:){0,2} (?P<kind>error|note|warning): ",
)

# `_generated_with` = "mypy <version> <PLATFORM_FLAGS_TEXT>".
_GENERATED_WITH_RE = re.compile(r"^(?P<version>mypy \S+) (?P<flags>.+)$")

_MAX_LISTED = 40


class ToolError(Exception):
    """mypy missing/crashed, output unparsable, or a corrupt baseline -> exit 2."""


@dataclass(frozen=True)
class MypyRun:
    returncode: int
    stdout: str
    stderr: str
    version: str


@dataclass(frozen=True)
class Baseline:
    counts: dict[str, int]
    total: int
    generated_with: str | None


@dataclass
class Comparison:
    total: int
    baseline_total: int
    files: int
    # (path, current, baseline); baseline is None for a file absent from the baseline.
    offenders: list[tuple[str, int, int | None]] = field(default_factory=list)
    improved: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def total_grew(self) -> bool:
        return self.total > self.baseline_total

    @property
    def passed(self) -> bool:
        return not self.offenders and not self.total_grew


def _posix(raw: str) -> str:
    """Normalise a mypy-printed path (os.sep on the host) to a forward-slash relative path."""
    return PureWindowsPath(raw).as_posix()


def parse_mypy_output(text: str) -> dict[str, int]:
    """Per-file ``error:`` counts from ``--no-error-summary`` mypy stdout.

    ``note:`` / ``warning:`` lines are recognised and ignored. Any other
    non-blank line is unexpected output and raises ``ToolError`` (fail closed:
    a format drift must not silently read as "0 errors").
    """
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _DIAG_RE.match(line)
        if match is None:
            raise ToolError(f"unparsable mypy output line: {line[:200]!r}")
        if match.group("kind") == "error":
            path = _posix(match.group("path"))
            counts[path] = counts.get(path, 0) + 1
    return counts


def _bad_baseline(why: str) -> ToolError:
    """A baseline that cannot be read: say how to replace it (the plain regenerate is refused)."""
    return ToolError(f"{why} — {REPLACE_HINT}")


def load_baseline(path: Path) -> Baseline:
    """Read and validate the committed baseline; any inconsistency raises ``ToolError``."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _bad_baseline(f"cannot read baseline {path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise _bad_baseline(f"baseline {path.name} is not a JSON object")
    total = raw.get(TOTAL_KEY)
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise _bad_baseline(f"baseline {path.name}: {TOTAL_KEY} missing or not a non-negative int")
    generated_with = raw.get(GENERATED_WITH_KEY)
    if generated_with is not None and not isinstance(generated_with, str):
        raise _bad_baseline(f"baseline {path.name}: {GENERATED_WITH_KEY} is not a string")
    counts: dict[str, int] = {}
    for key, value in raw.items():
        if key in RESERVED_KEYS:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise _bad_baseline(f"baseline {path.name}: {key!r} is not a positive int count")
        counts[key] = value
    if sum(counts.values()) != total:
        raise _bad_baseline(
            f"baseline {path.name}: {TOTAL_KEY}={total} != sum of per-file counts"
            f" ({sum(counts.values())}) (hand-edited?)",
        )
    return Baseline(counts=counts, total=total, generated_with=generated_with)


def baseline_mypy_version(baseline: Baseline) -> str:
    """The mypy version a baseline was written with, after checking its recorded flags.

    A baseline written without (or with different) ``PLATFORM_FLAGS`` was counted under other
    rules than this run uses, so comparing against it could pass or fail for the wrong
    reason; refuse with a tool error (exit 2) instead of passing silently.
    """
    match = _GENERATED_WITH_RE.match(baseline.generated_with or "")
    if match is None or match.group("flags") != PLATFORM_FLAGS_TEXT:
        raise ToolError(
            f"baseline {GENERATED_WITH_KEY} {baseline.generated_with!r} does not record"
            f" the flags this ratchet runs mypy with ({PLATFORM_FLAGS_TEXT!r})"
            f" — regenerate with {WRITE_HINT} (--allow-increase if any count rose)"
        )
    return match.group("version")


def compare(current: dict[str, int], baseline: Baseline) -> Comparison:
    """Ratchet comparison of current per-file counts against the baseline."""
    result = Comparison(
        total=sum(current.values()), baseline_total=baseline.total, files=len(current)
    )
    for path in sorted(current):
        new = current[path]
        old = baseline.counts.get(path)
        if old is None or new > old:
            result.offenders.append((path, new, old))
        elif new < old:
            result.improved.append((path, new, old))
    # A baseline file with no remaining errors is absent from `current` (count 0).
    for path in sorted(set(baseline.counts) - set(current)):
        result.improved.append((path, 0, baseline.counts[path]))
    result.improved.sort()
    return result


def _offender_lines(result: Comparison) -> list[str]:
    lines: list[str] = []
    for path, new, old in result.offenders[:_MAX_LISTED]:
        base = "not in baseline" if old is None else f"baseline {old}"
        lines.append(f"  {path}: {new} errors ({base})")
    if len(result.offenders) > _MAX_LISTED:
        lines.append(f"  ... and {len(result.offenders) - _MAX_LISTED} more offending files")
    return lines


def format_report(result: Comparison, *, baseline_version: str | None, version: str) -> list[str]:
    """Report lines: offenders, the one-line verdict, then notes."""
    lines = _offender_lines(result)
    verdict = "PASS" if result.passed else "FAIL"
    lines.append(
        f"mypy ratchet: {result.total} errors in {result.files} files"
        f" (baseline {result.baseline_total}) — {verdict}"
    )
    if not result.passed:
        if result.total_grew and not result.offenders:
            lines.append(f"  total grew: {result.total} > baseline {result.baseline_total}")
        lines.append(
            "  fix the new errors (python -m mypy --config-file=pyproject.toml <file>);"
            " do not raise the baseline without a stated reason"
        )
        if any(old is None for _, _, old in result.offenders):
            lines.append(
                "  new or renamed test file: fix its errors, or run --write-baseline"
                " (refused if any count rose; a rename needs --allow-increase)"
            )
        if baseline_version is not None and baseline_version != version:
            lines.append(
                f"  note: baseline was generated with {baseline_version}, running {version}"
                " — a mypy upgrade can shift counts; regenerate deliberately if so"
            )
    if result.improved:
        if result.passed:
            lines.append(
                f"mypy ratchet: {len(result.improved)} file(s) dropped below baseline"
                f" (total {result.total} < {result.baseline_total}); run `{WRITE_HINT}`"
                " to lock in the gain"
            )
        else:
            lines.append(
                f"mypy ratchet: {len(result.improved)} file(s) dropped below baseline;"
                f" once the failures above are fixed, run `{WRITE_HINT}` to lock in the gain"
            )
    return lines


def run_mypy() -> MypyRun:
    """Run mypy over the test trees; ``ToolError`` when it is not installed."""
    if importlib.util.find_spec("mypy") is None:
        raise ToolError("mypy is not installed in this interpreter (uv sync --extra dev)")
    proc = subprocess.run(  # noqa: S603 — argv is a module constant
        [sys.executable, *MYPY_ARGV],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return MypyRun(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        version=f"mypy {importlib.metadata.version('mypy')}",
    )


def collect_counts(run: MypyRun) -> dict[str, int]:
    """Per-file error counts from a mypy run; fail closed on anything but exit 0/1."""
    if run.returncode not in (0, 1):
        tail = (run.stderr.strip() or run.stdout.strip()).splitlines()[-3:]
        raise ToolError(f"mypy exited {run.returncode}: {' | '.join(tail)}")
    counts = parse_mypy_output(run.stdout)
    if run.returncode == 1 and not counts:
        # Exit 1 means "errors found"; none parsed means the output is not what we think it is.
        raise ToolError("mypy exited 1 but no `error:` lines were parsed")
    if run.returncode == 0 and counts:
        raise ToolError("mypy exited 0 but reported errors")
    return counts


def write_baseline(
    counts: dict[str, int], version: str, path: Path, *, allow_increase: bool
) -> int:
    """Regenerate the baseline; without ``allow_increase`` nothing may loosen.

    Refused (exit 1) when any file's count would rise above its committed value, a file
    absent from the committed baseline has any error, or the total would rise — checked
    per file, not only on ``_total``, because the tightening nudge is print-only and the
    committed baseline routinely carries slack that a total-only check would let absorb
    a new or grown file. A committed baseline that cannot be read is replaced only with
    ``allow_increase`` (there is nothing to compare against).
    """
    new_total = sum(counts.values())
    if path.exists():
        try:
            committed = load_baseline(path)
        except ToolError as exc:
            if not allow_increase:
                raise
            print(f"mypy ratchet: replacing an unreadable committed baseline ({exc})")
        else:
            result = compare(counts, committed)
            if not allow_increase and not result.passed:
                for line in _offender_lines(result):
                    print(line)
                print(
                    f"mypy ratchet: refusing to write — {len(result.offenders)} file(s) above"
                    f" the committed baseline (new total {new_total}, committed"
                    f" {committed.total}); fix the new errors, or pass --allow-increase"
                    " and state the reason in the commit"
                )
                return 1
    payload: dict[str, object] = {
        GENERATED_WITH_KEY: f"{version} {PLATFORM_FLAGS_TEXT}",
        TOTAL_KEY: new_total,
        **counts,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"mypy ratchet: wrote {path.name}: {new_total} errors in {len(counts)} files"
        f" ({version} {PLATFORM_FLAGS_TEXT})"
    )
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[[], MypyRun] = run_mypy,
    baseline_path: Path = BASELINE_PATH,
) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="regenerate the baseline from a fresh mypy run (refuses any per-file or total rise)",
    )
    parser.add_argument(
        "--allow-increase",
        action="store_true",
        help="with --write-baseline: permit any rise, a new file or an unreadable baseline"
        " (state the reason in the commit)",
    )
    args = parser.parse_args(argv)
    if args.allow_increase and not args.write_baseline:
        parser.error("--allow-increase only applies with --write-baseline")

    try:
        run = runner()
        counts = collect_counts(run)
        if args.write_baseline:
            return write_baseline(
                counts, run.version, baseline_path, allow_increase=args.allow_increase
            )
        baseline = load_baseline(baseline_path)
        baseline_version = baseline_mypy_version(baseline)
    except ToolError as exc:
        print(f"mypy ratchet: tool error — {exc}")
        return 2

    result = compare(counts, baseline)
    for line in format_report(result, baseline_version=baseline_version, version=run.version):
        print(line)
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
