"""Tests for scripts/mypy_ratchet.py — the mypy ratchet over the test tree (#28).

Unit-level only: the parser, comparison, report and CLI paths are exercised on
synthetic mypy output and a synthetic baseline. Real mypy is never run here
(``main`` takes an injectable runner); the real run is the gate step itself.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from scripts.mypy_ratchet import (
    Baseline,
    MypyRun,
    ToolError,
    collect_counts,
    compare,
    format_report,
    load_baseline,
    main,
    parse_mypy_output,
)

from scripts import mypy_ratchet

VERSION = "mypy 9.9.9"

SAMPLE_OUTPUT = """\
tests/a/test_one.py:3: error: Function is missing a return type annotation  [no-untyped-def]
tests/a/test_one.py:9: error: Argument 1 has incompatible type  [arg-type]
tests/a/test_one.py:9: note: See https://mypy.readthedocs.io/
tests/b/test_two.py:5:12: error: Item "None" has no attribute "x"  [union-attr]
fair_cam/tests/test_three.py: error: Source file found twice  [import-not-found]
"""


def _write_baseline(path: Path, counts: dict[str, int], version: str = VERSION) -> None:
    payload: dict[str, object] = {"_generated_with": version, "_total": sum(counts.values())}
    payload.update(counts)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _runner(stdout: str, returncode: int = 1, stderr: str = "") -> Callable[[], MypyRun]:
    def run() -> MypyRun:
        return MypyRun(returncode=returncode, stdout=stdout, stderr=stderr, version=VERSION)

    return run


def _errors(counts: dict[str, int]) -> str:
    """Synthetic mypy stdout carrying exactly ``counts`` errors per file."""
    lines = [
        f"{path}:{n + 1}: error: synthetic  [misc]"
        for path, count in counts.items()
        for n in range(count)
    ]
    return "\n".join(lines) + "\n"


# --- parser -----------------------------------------------------------------


def test_parse_counts_errors_per_file_and_ignores_notes() -> None:
    assert parse_mypy_output(SAMPLE_OUTPUT) == {
        "tests/a/test_one.py": 2,
        "tests/b/test_two.py": 1,
        "fair_cam/tests/test_three.py": 1,
    }


def test_parse_normalises_windows_separators() -> None:
    out = "tests\\a\\test_one.py:3: error: x  [misc]\ntests/a/test_one.py:4: error: y  [misc]\n"
    assert parse_mypy_output(out) == {"tests/a/test_one.py": 2}


def test_parse_empty_and_blank_output_is_zero_errors() -> None:
    assert parse_mypy_output("") == {}
    assert parse_mypy_output("\n\n") == {}


def test_parse_unrecognised_line_fails_closed() -> None:
    with pytest.raises(ToolError, match="unparsable"):
        parse_mypy_output("tests/a.py:3: error: ok  [misc]\nSomething went wrong\n")


# --- collect_counts (exit-code handling) -------------------------------------


def test_collect_counts_rejects_crash_exit_code() -> None:
    run = MypyRun(returncode=2, stdout="", stderr="boom\n", version=VERSION)
    with pytest.raises(ToolError, match="exited 2"):
        collect_counts(run)


def test_collect_counts_rejects_exit_1_without_parsed_errors() -> None:
    run = MypyRun(returncode=1, stdout="", stderr="No module named mypy\n", version=VERSION)
    with pytest.raises(ToolError, match="no `error:` lines"):
        collect_counts(run)


def test_collect_counts_clean_run_is_empty() -> None:
    assert collect_counts(MypyRun(returncode=0, stdout="", stderr="", version=VERSION)) == {}


# --- baseline loading -----------------------------------------------------------


def test_load_baseline_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    _write_baseline(path, {"tests/a.py": 2, "tests/b.py": 3})
    assert load_baseline(path) == Baseline(
        counts={"tests/a.py": 2, "tests/b.py": 3}, total=5, generated_with=VERSION
    )


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        json.dumps({"tests/a.py": 1}),  # no _total
        json.dumps({"_total": 2, "tests/a.py": 1}),  # total != sum
        json.dumps({"_total": 0, "tests/a.py": 0}),  # zero entry is not a baseline entry
        json.dumps({"_total": 1, "tests/a.py": "1"}),  # non-int count
        json.dumps({"_total": True}),  # bool is not a count
    ],
)
def test_load_baseline_rejects_corrupt_files(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ToolError):
        load_baseline(path)


def test_load_baseline_missing_file_is_a_tool_error(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="cannot read baseline"):
        load_baseline(tmp_path / "absent.json")


# --- comparison ---------------------------------------------------------------------


def _baseline(counts: dict[str, int]) -> Baseline:
    return Baseline(counts=counts, total=sum(counts.values()), generated_with=VERSION)


def test_compare_equal_passes_without_improvements() -> None:
    result = compare({"tests/a.py": 2}, _baseline({"tests/a.py": 2}))
    assert result.passed
    assert result.offenders == [] and result.improved == []


def test_compare_count_up_fails() -> None:
    result = compare({"tests/a.py": 3}, _baseline({"tests/a.py": 2}))
    assert not result.passed
    assert result.offenders == [("tests/a.py", 3, 2)]


def test_compare_new_file_with_errors_fails() -> None:
    result = compare({"tests/a.py": 2, "tests/new.py": 1}, _baseline({"tests/a.py": 2}))
    assert not result.passed
    assert result.offenders == [("tests/new.py", 1, None)]


def test_compare_count_down_passes_and_is_reported_as_improved() -> None:
    result = compare({"tests/a.py": 1}, _baseline({"tests/a.py": 2}))
    assert result.passed
    assert result.improved == [("tests/a.py", 1, 2)]


def test_compare_file_fully_fixed_is_improved_to_zero() -> None:
    result = compare({"tests/a.py": 2}, _baseline({"tests/a.py": 2, "tests/b.py": 4}))
    assert result.passed
    assert result.improved == [("tests/b.py", 0, 4)]


def test_compare_total_growth_fails_even_if_offenders_are_empty() -> None:
    # A baseline whose _total was edited below its per-file sum is rejected at
    # load time; this pins the comparison's own total guard independently.
    result = compare(
        {"tests/a.py": 2}, Baseline(counts={"tests/a.py": 2}, total=1, generated_with=None)
    )
    assert result.offenders == []
    assert result.total_grew and not result.passed


def test_compare_one_file_down_cannot_hide_another_file_up() -> None:
    result = compare(
        {"tests/a.py": 1, "tests/b.py": 5}, _baseline({"tests/a.py": 4, "tests/b.py": 2})
    )
    assert not result.passed
    assert result.offenders == [("tests/b.py", 5, 2)]


# --- report -------------------------------------------------------------------------------


def test_report_pass_line_is_exact() -> None:
    result = compare({"tests/a.py": 2}, _baseline({"tests/a.py": 2}))
    assert format_report(result, baseline_version=VERSION, version=VERSION) == [
        "mypy ratchet: 2 errors in 1 files (baseline 2) — PASS"
    ]


def test_report_fail_lists_offenders_with_new_and_baseline_counts() -> None:
    result = compare({"tests/a.py": 3, "tests/new.py": 1}, _baseline({"tests/a.py": 2}))
    lines = format_report(result, baseline_version=VERSION, version=VERSION)
    assert "  tests/a.py: 3 errors (baseline 2)" in lines
    assert "  tests/new.py: 1 errors (not in baseline)" in lines
    assert "mypy ratchet: 4 errors in 2 files (baseline 2) — FAIL" in lines


def test_report_pass_with_improvement_suggests_write_baseline() -> None:
    result = compare({"tests/a.py": 1}, _baseline({"tests/a.py": 2}))
    lines = format_report(result, baseline_version=VERSION, version=VERSION)
    assert lines[0] == "mypy ratchet: 1 errors in 1 files (baseline 2) — PASS"
    assert len(lines) == 2
    assert "--write-baseline" in lines[1] and "1 file(s) dropped below baseline" in lines[1]


def test_report_flags_mypy_version_drift_only_on_failure() -> None:
    failing = compare({"tests/a.py": 3}, _baseline({"tests/a.py": 2}))
    drift = format_report(failing, baseline_version="mypy 1.0.0", version=VERSION)
    assert any("generated with mypy 1.0.0" in line for line in drift)
    passing = compare({"tests/a.py": 2}, _baseline({"tests/a.py": 2}))
    assert len(format_report(passing, baseline_version="mypy 1.0.0", version=VERSION)) == 1


def test_report_caps_the_offender_listing() -> None:
    current = {f"tests/f{n:03d}.py": 1 for n in range(60)}
    lines = format_report(compare(current, _baseline({})), baseline_version=None, version=VERSION)
    assert sum(line.startswith("  tests/f") for line in lines) == 40
    assert any("and 20 more offending files" in line for line in lines)


# --- CLI: check mode -------------------------------------------------------------------------


def test_main_equal_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2})
    rc = main([], runner=_runner(_errors({"tests/a.py": 2})), baseline_path=base)
    assert rc == 0
    assert (
        capsys.readouterr().out.strip() == "mypy ratchet: 2 errors in 1 files (baseline 2) — PASS"
    )


def test_main_count_up_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2})
    rc = main([], runner=_runner(_errors({"tests/a.py": 3})), baseline_path=base)
    out = capsys.readouterr().out
    assert rc == 1
    assert "tests/a.py: 3 errors (baseline 2)" in out and out.count("— FAIL") == 1


def test_main_new_file_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2})
    rc = main([], runner=_runner(_errors({"tests/a.py": 2, "tests/new.py": 1})), baseline_path=base)
    assert rc == 1
    assert "tests/new.py: 1 errors (not in baseline)" in capsys.readouterr().out


def test_main_count_down_passes_with_tightening_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2})
    rc = main([], runner=_runner(_errors({"tests/a.py": 1})), baseline_path=base)
    out = capsys.readouterr().out
    assert rc == 0
    assert "— PASS" in out and "--write-baseline" in out
    assert json.loads(base.read_text(encoding="utf-8"))["_total"] == 2  # never auto-rewritten


def test_main_all_errors_fixed_passes_when_mypy_exits_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2})
    rc = main([], runner=_runner("", returncode=0), baseline_path=base)
    assert rc == 0
    assert "mypy ratchet: 0 errors in 0 files (baseline 2) — PASS" in capsys.readouterr().out


def test_main_missing_baseline_is_tool_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main([], runner=_runner(_errors({"tests/a.py": 1})), baseline_path=tmp_path / "none.json")
    assert rc == 2
    assert "tool error" in capsys.readouterr().out


def test_main_mypy_unavailable_is_tool_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing() -> MypyRun:
        raise ToolError("mypy is not installed in this interpreter")

    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 1})
    assert main([], runner=missing, baseline_path=base) == 2
    assert "tool error" in capsys.readouterr().out


def test_main_unparsable_output_is_tool_error(tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 1})
    assert main([], runner=_runner("garbage line\n"), baseline_path=base) == 2


def test_main_mypy_crash_is_tool_error(tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 1})
    assert main([], runner=_runner("", returncode=2, stderr="crash"), baseline_path=base) == 2


# --- CLI: --write-baseline ----------------------------------------------------------------------


def test_write_baseline_creates_sorted_file(tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    out = _errors({"tests/b.py": 1, "fair_cam/tests/z.py": 2, "tests/a.py": 3})
    assert main(["--write-baseline"], runner=_runner(out), baseline_path=base) == 0
    data = json.loads(base.read_text(encoding="utf-8"))
    assert data["_total"] == 6 and data["_generated_with"] == VERSION
    assert list(data) == [
        "_generated_with",
        "_total",
        "fair_cam/tests/z.py",
        "tests/a.py",
        "tests/b.py",
    ]
    assert base.read_text(encoding="utf-8").endswith("\n")


def test_write_baseline_refuses_increase_without_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2})
    before = base.read_bytes()
    rc = main(["--write-baseline"], runner=_runner(_errors({"tests/a.py": 3})), baseline_path=base)
    assert rc == 1
    assert "--allow-increase" in capsys.readouterr().out
    assert base.read_bytes() == before


def test_write_baseline_allows_increase_with_flag(tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2})
    rc = main(
        ["--write-baseline", "--allow-increase"],
        runner=_runner(_errors({"tests/a.py": 3})),
        baseline_path=base,
    )
    assert rc == 0
    assert json.loads(base.read_text(encoding="utf-8"))["_total"] == 3


def test_write_baseline_tightens_a_decrease_without_flag(tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 3, "tests/b.py": 1})
    rc = main(["--write-baseline"], runner=_runner(_errors({"tests/a.py": 2})), baseline_path=base)
    assert rc == 0
    assert load_baseline(base).counts == {"tests/a.py": 2}


def test_write_baseline_is_idempotent(tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    runner = _runner(_errors({"tests/a.py": 2, "tests/b.py": 1}))
    assert main(["--write-baseline"], runner=runner, baseline_path=base) == 0
    first = base.read_bytes()
    assert main(["--write-baseline"], runner=runner, baseline_path=base) == 0
    assert base.read_bytes() == first
    # And the file it wrote passes the check against the same output.
    assert main([], runner=runner, baseline_path=base) == 0


def test_allow_increase_alone_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--allow-increase"], runner=_runner(""), baseline_path=tmp_path / "b.json")
    assert excinfo.value.code == 2


# --- wiring / committed baseline -----------------------------------------------------------------------


def test_mypy_argv_covers_both_test_trees_with_the_pyproject_config() -> None:
    argv = mypy_ratchet.MYPY_ARGV
    assert "--config-file=pyproject.toml" in argv
    assert "--no-error-summary" in argv and "--show-error-codes" in argv
    assert "tests" in argv and str(Path("fair_cam") / "tests") in argv


def test_committed_baseline_parses_and_is_consistent() -> None:
    baseline = load_baseline(mypy_ratchet.BASELINE_PATH)
    assert baseline.total >= 0
    assert baseline.generated_with is not None and baseline.generated_with.startswith("mypy ")
    assert list(baseline.counts) == sorted(baseline.counts)
    assert all(p.startswith(("tests/", "fair_cam/tests/")) for p in baseline.counts)
