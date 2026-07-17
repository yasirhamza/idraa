# tests/contracts/test_lint_adapter_iter.py
"""Tests for scripts/lint_adapter_iter.py — the κ-class index-on-list lint rule."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

_LINT = Path("scripts/lint_adapter_iter.py")


def _write_temp_file(tmp_path: Path, name: str, content: str) -> Path:
    """Helper: write content to tmp_path/name and return the absolute path."""
    file = tmp_path / name
    file.write_text(dedent(content), encoding="utf-8")
    return file


def _run_lint(*args: str | Path) -> subprocess.CompletedProcess[str]:
    """Run the lint script; return CompletedProcess for assertion."""
    return subprocess.run(
        [sys.executable, str(_LINT), *(str(a) for a in args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_flags_index_zero(tmp_path: Path) -> None:
    """xs[0] in a scoped file is flagged."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def foo(xs: list[int]) -> int:
            return xs[0]
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0
    assert "[0]" in result.stdout or "[0]" in result.stderr


def test_flags_index_minus_one(tmp_path: Path) -> None:
    """xs[-1] is flagged."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def foo(xs: list[int]) -> int:
            return xs[-1]
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0
    assert "[-1]" in result.stdout or "[-1]" in result.stderr


def test_flags_next_iter(tmp_path: Path) -> None:
    """next(iter(xs)) is flagged."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def foo(xs: list[int]) -> int:
            return next(iter(xs))
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0
    assert "next(iter" in result.stdout or "next(iter" in result.stderr


def test_flags_list_pop(tmp_path: Path) -> None:
    """xs.pop() is flagged."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def foo(xs: list[int]) -> int:
            return xs.pop()
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0
    assert ".pop()" in result.stdout or ".pop()" in result.stderr


def test_flags_list_paren_zero(tmp_path: Path) -> None:
    """list(xs)[0] is flagged AND labeled as the more-specific pattern.

    Detector ordering matters: list(xs)[0] is also a Subscript with
    constant 0, so a naive ordering would label it as plain [0]. The
    most-specific detector (list-paren-zero) must run first.
    """
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def foo(xs: list[int]) -> int:
            return list(xs)[0]
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "list(...)[0]" in output, (
        f"expected the more-specific 'list(...)[0]' label; got:\n{output}"
    )


def test_accepts_opt_out_comment(tmp_path: Path) -> None:
    """xs[0]  # adapter-iter: ok — reason  passes the lint."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def foo(xs: list[int]) -> int:
            return xs[0]  # adapter-iter: ok — first row is canonical user
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected violation:\n{result.stdout}{result.stderr}"


def test_rejects_opt_out_without_reason(tmp_path: Path) -> None:
    """# adapter-iter: ok  with no reason fails — forces the author to justify."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def foo(xs: list[int]) -> int:
            return xs[0]  # adapter-iter: ok
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0
    assert "reason" in result.stdout.lower() or "reason" in result.stderr.lower()


def test_rejects_single_hyphen_separator(tmp_path: Path) -> None:
    """# adapter-iter: ok-foo (single hyphen, no space) fails — em-dash or ≥2 hyphens required.

    Without this guard, the regex misclassifies adjacent hyphens as
    separator-plus-reason: e.g., "ok-foo" parses as separator='-' +
    reason='foo' which is unintentionally accepted. Em-dash or two-or-more
    hyphens is the canonical separator.
    """
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def foo(xs: list[int]) -> int:
            return xs[0]  # adapter-iter: ok-foo
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0


def test_only_scans_passed_files(tmp_path: Path) -> None:
    """Lint scans the files passed on argv; non-passed files are not implicitly scanned."""
    bad = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def foo(xs: list[int]) -> int:
            return xs[0]
        """,
    )
    good = _write_temp_file(
        tmp_path,
        "other.py",
        """
        def foo(xs: list[int]) -> int:
            return sum(xs)
        """,
    )

    # Pass only the GOOD file. Lint should pass even though bad.py also exists.
    result = _run_lint(good)
    assert result.returncode == 0

    # Now pass the BAD file. Lint must fail.
    result_bad = _run_lint(bad)
    assert result_bad.returncode != 0
