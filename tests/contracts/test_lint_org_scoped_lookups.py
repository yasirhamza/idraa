# tests/contracts/test_lint_org_scoped_lookups.py
"""Tests for scripts/lint_org_scoped_lookups.py — the bare-PK org-scoping lint rule."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

_LINT = Path("scripts/lint_org_scoped_lookups.py")


def _write_temp_file(tmp_path: Path, name: str, content: str) -> Path:
    file = tmp_path / name
    file.write_text(dedent(content), encoding="utf-8")
    return file


def _run_lint(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_LINT), *(str(a) for a in args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_flags_bare_get_on_org_scoped_model(tmp_path: Path) -> None:
    """db.get(Control, control_id) with no org check anywhere in the function is flagged."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def get_control(db, control_id):
            return await db.get(Control, control_id)
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0
    assert "Control" in (result.stdout + result.stderr)


def test_passes_with_inline_organization_id_check_after_the_call(tmp_path: Path) -> None:
    """An organization_id Compare ANYWHERE in the function (even after the .get) clears it."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def load(db, control_id, org):
            control = await db.get(Control, control_id)
            if control is None or control.organization_id != org.id:
                raise NotFound()
            return control
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected violation:\n{result.stdout}{result.stderr}"


def test_passes_with_inline_organization_id_check_before_the_call(tmp_path: Path) -> None:
    """Order independence: a check BEFORE the .get() call also clears it."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def load(db, control_id, org, existing):
            if existing.organization_id != org.id:
                raise NotFound()
            return await db.get(Control, control_id)
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected violation:\n{result.stdout}{result.stderr}"


def test_passes_when_function_delegates_to_a_for_org_helper(tmp_path: Path) -> None:
    """Delegated safety: a *_for_org* call anywhere in the function counts as verification.

    Mirrors the real services/runs.py pattern: RunRepo(db).get_for_org_or_raise(...)
    resolves ownership, THEN a bare db.get(RunSamples, run_id) is safe (1:1 with the
    already-verified run). No inline organization_id Compare exists at all here.
    """
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def purge_samples(self, run_id, *, org_id):
            await RunRepo(self._db).get_for_org_or_raise(org_id, run_id)
            row = await self._db.get(RunSamples, run_id)
            return row
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected violation:\n{result.stdout}{result.stderr}"


def test_accepts_opt_out_comment_on_formatter_wrapped_call(tmp_path: Path) -> None:
    """A long call ruff wraps across lines puts the comment on the LAST line
    (its actual reflow behavior, real example: routes/step_up.py) — the
    opt-out search must cover the call's full line span, not just its
    AST-reported lineno (the first line), or a formatter pass silently
    breaks a previously-valid suppression."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def load(db, control_id):
            control = (
                await db.get(Control, control_id) or control_id
            )  # org-scope: ok — reflowed by ruff, comment trails the close-paren
            return control
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected violation:\n{result.stdout}{result.stderr}"


def test_opt_out_does_not_cross_contaminate_sibling_statements(tmp_path: Path) -> None:
    """Two org-scoped calls in the SAME outer block (an if-body): suppressing
    one must NOT silently suppress the other. Guards against widening the
    opt-out search to an over-broad enclosing statement (e.g. the whole `if`)
    instead of the innermost one (just that one assignment)."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def load(db, control_id, run_id):
            if True:
                a = await db.get(Control, control_id)  # org-scope: ok — suppressed on purpose
                b = await db.get(RiskAnalysisRun, run_id)
            return a, b
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "RiskAnalysisRun" in output
    assert "Control" not in output


def test_accepts_opt_out_comment(tmp_path: Path) -> None:
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def load(db, control_id):
            return await db.get(Control, control_id)  # org-scope: ok — caller checks at all 8 sites
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected violation:\n{result.stdout}{result.stderr}"


def test_rejects_opt_out_without_reason(tmp_path: Path) -> None:
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def load(db, control_id):
            return await db.get(Control, control_id)  # org-scope: ok
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0
    assert "reason" in (result.stdout + result.stderr).lower()


def test_does_not_flag_excluded_organization_model(tmp_path: Path) -> None:
    """Organization itself isn't FK-scoped to an org (it IS the org) — never flagged."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def load(db, user):
            return await db.get(Organization, user.organization_id)
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected violation:\n{result.stdout}{result.stderr}"


def test_does_not_flag_excluded_auth_session_model(tmp_path: Path) -> None:
    """AuthSession is deliberately user-scoped, not org-scoped — never flagged."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def load(db, session_id):
            return await db.get(AuthSession, session_id)
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected violation:\n{result.stdout}{result.stderr}"


def test_does_not_flag_plain_dict_get(tmp_path: Path) -> None:
    """data.get("key") — a string literal arg, not a model name — is never flagged."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def load(data):
            return data.get("key")
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected violation:\n{result.stdout}{result.stderr}"


def test_does_not_flag_get_with_unrelated_name_arg(tmp_path: Path) -> None:
    """mapping.get(some_var) where some_var isn't a known org-scoped model class name."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def load(mapping, some_var):
            return mapping.get(some_var)
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected violation:\n{result.stdout}{result.stderr}"


def test_does_not_crash_on_get_with_no_args(tmp_path: Path) -> None:
    """dict-style .get() with zero args must not raise an IndexError in the scanner."""
    file = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        def load(data):
            return data.get()
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, f"unexpected crash/violation:\n{result.stdout}{result.stderr}"


def test_only_scans_passed_files(tmp_path: Path) -> None:
    bad = _write_temp_file(
        tmp_path,
        "scoped.py",
        """
        async def load(db, control_id):
            return await db.get(Control, control_id)
        """,
    )
    good = _write_temp_file(
        tmp_path,
        "other.py",
        """
        async def load(db, control_id, org):
            control = await db.get(Control, control_id)
            if control.organization_id != org.id:
                raise NotFound()
            return control
        """,
    )
    result = _run_lint(good)
    assert result.returncode == 0

    result_bad = _run_lint(bad)
    assert result_bad.returncode != 0


def test_all_flag_sweeps_the_real_tree_without_crashing() -> None:
    """--all must parse every routes/services/repositories file without a SyntaxError."""
    result = _run_lint("--all")
    assert result.returncode in (0, 1), (
        f"scanner crashed (exit {result.returncode}):\n{result.stdout}{result.stderr}"
    )
