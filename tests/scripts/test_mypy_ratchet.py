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
    PLATFORM_FLAGS_TEXT,
    Baseline,
    MypyRun,
    ToolError,
    baseline_mypy_version,
    collect_counts,
    compare,
    format_report,
    load_baseline,
    main,
    parse_mypy_output,
)

from scripts import mypy_ratchet

VERSION = "mypy 9.9.9"
GENERATED_WITH = f"{VERSION} {PLATFORM_FLAGS_TEXT}"

SAMPLE_OUTPUT = """\
tests/a/test_one.py:3: error: Function is missing a return type annotation  [no-untyped-def]
tests/a/test_one.py:9: error: Argument 1 has incompatible type  [arg-type]
tests/a/test_one.py:9: note: See https://mypy.readthedocs.io/
tests/b/test_two.py:5:12: error: Item "None" has no attribute "x"  [union-attr]
fair_cam/tests/test_three.py: error: Source file found twice  [import-not-found]
"""


def _write_baseline(
    path: Path, counts: dict[str, int], generated_with: str | None = GENERATED_WITH
) -> None:
    payload: dict[str, object] = {"_total": sum(counts.values())}
    if generated_with is not None:
        payload["_generated_with"] = generated_with
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
        counts={"tests/a.py": 2, "tests/b.py": 3}, total=5, generated_with=GENERATED_WITH
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
    return Baseline(counts=counts, total=sum(counts.values()), generated_with=GENERATED_WITH)


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
    assert data["_total"] == 6 and data["_generated_with"] == GENERATED_WITH
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


def test_mypy_argv_gives_the_ratchet_its_own_cache_dir() -> None:
    # The gate's "mypy" step (host platform) and this step (--platform linux) must not share
    # one mypy cache: mypy keys entries on the options, so they would rewrite each other's
    # entries on every macOS push. Under .mypy_cache/ so the existing ignore rules cover it.
    cache_args = [a for a in mypy_ratchet.MYPY_ARGV if a.startswith("--cache-dir=")]
    assert cache_args == [f"--cache-dir={Path('.mypy_cache') / 'ratchet'}"]
    assert Path(cache_args[0].split("=", 1)[1]).parts[0] == ".mypy_cache"


def test_mypy_argv_pins_platform_and_python_version() -> None:
    # Host-independent counts: macOS dev machines and the Linux CI runner must agree.
    argv = list(mypy_ratchet.MYPY_ARGV)
    assert argv[argv.index("--platform") + 1] == "linux"
    assert argv[argv.index("--python-version") + 1] == "3.11"
    assert PLATFORM_FLAGS_TEXT == "--platform linux --python-version 3.11"


def test_committed_baseline_parses_and_is_consistent() -> None:
    baseline = load_baseline(mypy_ratchet.BASELINE_PATH)
    assert baseline.total >= 0
    assert baseline.generated_with is not None and baseline.generated_with.startswith("mypy ")
    # The committed baseline records the flags the ratchet runs with (else the gate exits 2).
    assert baseline_mypy_version(baseline).startswith("mypy ")
    assert baseline.generated_with.endswith(PLATFORM_FLAGS_TEXT)
    assert list(baseline.counts) == sorted(baseline.counts)
    assert all(p.startswith(("tests/", "fair_cam/tests/")) for p in baseline.counts)


# --- recorded-flags check -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "generated_with",
    [
        None,  # key absent
        VERSION,  # written without any flags (the pre-#28-follow-up format)
        f"{VERSION} --platform darwin --python-version 3.11",
        f"{VERSION} --platform linux --python-version 3.12",
        f"{VERSION} --platform linux",
        f"{VERSION} {PLATFORM_FLAGS_TEXT} --strict",  # extra flags are a different rule set too
        PLATFORM_FLAGS_TEXT,  # flags without a mypy version
    ],
)
def test_main_refuses_a_baseline_without_the_ratchets_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], generated_with: str | None
) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2}, generated_with=generated_with)
    # Counts equal: without the flags check this would PASS.
    rc = main([], runner=_runner(_errors({"tests/a.py": 2})), baseline_path=base)
    out = capsys.readouterr().out
    assert rc == 2
    assert "tool error" in out and PLATFORM_FLAGS_TEXT in out and "— PASS" not in out


def test_baseline_mypy_version_returns_the_recorded_version() -> None:
    baseline = Baseline(counts={}, total=0, generated_with=f"mypy 1.2.3 {PLATFORM_FLAGS_TEXT}")
    assert baseline_mypy_version(baseline) == "mypy 1.2.3"


def test_write_baseline_regenerates_over_a_baseline_with_old_flags(tmp_path: Path) -> None:
    # The refusal above must not wedge the fix: --write-baseline replaces a flagless file.
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2}, generated_with=VERSION)
    rc = main(["--write-baseline"], runner=_runner(_errors({"tests/a.py": 2})), baseline_path=base)
    assert rc == 0
    assert load_baseline(base).generated_with == GENERATED_WITH
    assert main([], runner=_runner(_errors({"tests/a.py": 2})), baseline_path=base) == 0


def test_report_version_drift_note_uses_the_recorded_mypy_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2}, generated_with=f"mypy 1.0.0 {PLATFORM_FLAGS_TEXT}")
    rc = main([], runner=_runner(_errors({"tests/a.py": 3})), baseline_path=base)
    assert rc == 1
    assert "baseline was generated with mypy 1.0.0, running mypy 9.9.9" in capsys.readouterr().out


# --- strict --write-baseline: nothing loosens without --allow-increase ---------------------------------


def test_write_baseline_refuses_a_per_file_increase_hidden_by_a_slack_total(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Committed total 11; the new run totals 8 (<= 11) but b.py rose 1 -> 5 while a.py fell.
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 10, "tests/b.py": 1})
    before = base.read_bytes()
    out = _errors({"tests/a.py": 3, "tests/b.py": 5})
    rc = main(["--write-baseline"], runner=_runner(out), baseline_path=base)
    printed = capsys.readouterr().out
    assert rc == 1
    assert "tests/b.py: 5 errors (baseline 1)" in printed and "--allow-increase" in printed
    assert base.read_bytes() == before


def test_write_baseline_refuses_a_new_file_hidden_by_a_slack_total(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 10})
    before = base.read_bytes()
    out = _errors({"tests/a.py": 3, "tests/new.py": 4})  # total 7 < committed 10
    rc = main(["--write-baseline"], runner=_runner(out), baseline_path=base)
    printed = capsys.readouterr().out
    assert rc == 1
    assert "tests/new.py: 4 errors (not in baseline)" in printed
    assert base.read_bytes() == before


def test_write_baseline_refuses_a_rename_without_the_flag(tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/old_name.py": 3})
    rc = main(
        ["--write-baseline"],
        runner=_runner(_errors({"tests/new_name.py": 3})),
        baseline_path=base,
    )
    assert rc == 1
    rc = main(
        ["--write-baseline", "--allow-increase"],
        runner=_runner(_errors({"tests/new_name.py": 3})),
        baseline_path=base,
    )
    assert rc == 0
    assert load_baseline(base).counts == {"tests/new_name.py": 3}


def test_write_baseline_allow_increase_writes_per_file_and_new_file_rises(tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 10, "tests/b.py": 1})
    out = _errors({"tests/a.py": 3, "tests/b.py": 5, "tests/new.py": 2})
    rc = main(["--write-baseline", "--allow-increase"], runner=_runner(out), baseline_path=base)
    assert rc == 0
    assert load_baseline(base).counts == {"tests/a.py": 3, "tests/b.py": 5, "tests/new.py": 2}


def test_write_baseline_accepts_pure_drops_and_removals(tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 10, "tests/b.py": 1, "tests/c.py": 2})
    # a drops, b is fixed entirely (absent from the output), c is unchanged.
    out = _errors({"tests/a.py": 4, "tests/c.py": 2})
    assert main(["--write-baseline"], runner=_runner(out), baseline_path=base) == 0
    assert load_baseline(base).counts == {"tests/a.py": 4, "tests/c.py": 2}


def test_write_baseline_accepts_equal_counts(tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 2})
    out = _errors({"tests/a.py": 2})
    assert main(["--write-baseline"], runner=_runner(out), baseline_path=base) == 0


# --- unreadable baseline: a remedy that actually runs ------------------------------------------------------


def test_unreadable_baseline_message_names_a_runnable_remedy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "baseline.json"
    base.write_text(json.dumps({"_total": 99, "tests/a.py": 1}), encoding="utf-8")  # sum mismatch
    rc = main([], runner=_runner(_errors({"tests/a.py": 1})), baseline_path=base)
    out = capsys.readouterr().out
    assert rc == 2
    assert "--write-baseline --allow-increase" in out and "delete the file" in out


def test_write_baseline_over_an_unreadable_baseline_needs_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "baseline.json"
    base.write_text("{not json", encoding="utf-8")
    runner = _runner(_errors({"tests/a.py": 2}))
    assert main(["--write-baseline"], runner=runner, baseline_path=base) == 2
    assert "--allow-increase" in capsys.readouterr().out
    assert base.read_text(encoding="utf-8") == "{not json"  # untouched
    assert main(["--write-baseline", "--allow-increase"], runner=runner, baseline_path=base) == 0
    assert load_baseline(base).counts == {"tests/a.py": 2}
    assert main([], runner=runner, baseline_path=base) == 0


# --- report messages --------------------------------------------------------------------------------------------


def test_report_not_in_baseline_adds_one_rename_hint() -> None:
    current = {"tests/new_a.py": 1, "tests/new_b.py": 2}
    lines = format_report(compare(current, _baseline({})), baseline_version=None, version=VERSION)
    hints = [line for line in lines if "new or renamed test file" in line]
    assert len(hints) == 1  # one hint, however many files
    assert "run --write-baseline" in hints[0] and "refused if any count rose" in hints[0]


def test_report_rename_hint_absent_when_every_offender_is_in_the_baseline() -> None:
    result = compare({"tests/a.py": 3}, _baseline({"tests/a.py": 2}))
    lines = format_report(result, baseline_version=None, version=VERSION)
    assert not any("new or renamed" in line for line in lines)


def test_report_fail_also_prints_the_tightening_note_when_a_file_dropped() -> None:
    result = compare(
        {"tests/a.py": 1, "tests/b.py": 5}, _baseline({"tests/a.py": 4, "tests/b.py": 2})
    )
    lines = format_report(result, baseline_version=None, version=VERSION)
    assert any("— FAIL" in line for line in lines)
    notes = [line for line in lines if "dropped below baseline" in line]
    assert len(notes) == 1 and "--write-baseline" in notes[0]


def test_main_fail_run_with_a_drop_prints_the_tightening_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "baseline.json"
    _write_baseline(base, {"tests/a.py": 4, "tests/b.py": 2})
    rc = main([], runner=_runner(_errors({"tests/a.py": 1, "tests/b.py": 5})), baseline_path=base)
    out = capsys.readouterr().out
    assert rc == 1
    assert "1 file(s) dropped below baseline" in out
