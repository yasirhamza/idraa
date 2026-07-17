# tests/contracts/test_lint_alembic_uuids.py
"""Tests for scripts/lint_alembic_uuids.py — the seed-UUID format foot-gun lint.

The foot-gun (recurred 4×, repaired by migrations b3e9c1a47d52 +
e7d0c3a91f2b): raw-text seed INSERTs that bind ``str(uuid.uuid4())`` store
the 36-char hyphenated form, while the ORM ``Uuid`` type binds 32-char
no-hyphen hex — every id-based lookup then silently 404s. The lint blocks
the *string* forms (``str(uuid4())``, f-string interpolation, hyphenated
literals) in new migrations; bare ``uuid.uuid4()`` bound through ORM-typed
columns is the legitimate pattern and passes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

_LINT = Path("scripts/lint_alembic_uuids.py")


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


def test_flags_str_uuid4(tmp_path: Path) -> None:
    """str(uuid.uuid4()) binds the hyphenated form — flagged."""
    file = _write_temp_file(
        tmp_path,
        "20990101_seed_things.py",
        """
        import uuid

        def upgrade():
            rows = [{"id": str(uuid.uuid4()), "name": "x"}]
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0
    assert ".hex" in result.stdout + result.stderr


def test_flags_str_bare_uuid4(tmp_path: Path) -> None:
    """str(uuid4()) (from-import form) — flagged."""
    file = _write_temp_file(
        tmp_path,
        "20990101_seed_things.py",
        """
        from uuid import uuid4

        def upgrade():
            eid = str(uuid4())
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0


def test_flags_fstring_interpolation(tmp_path: Path) -> None:
    """f-string {uuid.uuid4()} interpolates the hyphenated form — flagged."""
    file = _write_temp_file(
        tmp_path,
        "20990101_seed_things.py",
        """
        import uuid

        def upgrade():
            sql = f"INSERT INTO t (id) VALUES ('{uuid.uuid4()}')"
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0


def test_flags_hyphenated_uuid_literal(tmp_path: Path) -> None:
    """A hardcoded 36-char hyphenated UUID literal — flagged."""
    file = _write_temp_file(
        tmp_path,
        "20990101_seed_things.py",
        """
        def upgrade():
            org_id = "123e4567-e89b-42d3-a456-426614174000"
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0


def test_passes_uuid4_hex(tmp_path: Path) -> None:
    """uuid4().hex is the prescribed form — passes."""
    file = _write_temp_file(
        tmp_path,
        "20990101_seed_things.py",
        """
        import uuid

        def upgrade():
            rows = [{"id": uuid.uuid4().hex, "name": "x"}]
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, result.stdout + result.stderr


def test_passes_bare_uuid4_orm_bind(tmp_path: Path) -> None:
    """Bare uuid.uuid4() bound through an ORM-typed column is legitimate."""
    file = _write_temp_file(
        tmp_path,
        "20990101_seed_things.py",
        """
        import uuid

        def upgrade():
            op.bulk_insert(table, [dict(id=uuid.uuid4(), name="x")])
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, result.stdout + result.stderr


def test_opt_out_with_reason_passes(tmp_path: Path) -> None:
    file = _write_temp_file(
        tmp_path,
        "20990101_seed_things.py",
        """
        import uuid

        def upgrade():
            legacy = str(uuid.uuid4())  # uuid-format: ok — column is TEXT display-only, never joined
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, result.stdout + result.stderr


def test_opt_out_without_reason_still_flags(tmp_path: Path) -> None:
    file = _write_temp_file(
        tmp_path,
        "20990101_seed_things.py",
        """
        import uuid

        def upgrade():
            legacy = str(uuid.uuid4())  # uuid-format: ok —
        """,
    )
    result = _run_lint(file)
    assert result.returncode != 0


def test_grandfathered_file_passes(tmp_path: Path) -> None:
    """Already-applied historical migrations are immutable — grandfathered by
    basename, never edited to satisfy the lint."""
    file = _write_temp_file(
        tmp_path,
        "c1d2e3f4a5b6_seed_library_entries.py",
        """
        import uuid

        def upgrade():
            rows = [{"id": str(uuid.uuid4())}]
        """,
    )
    result = _run_lint(file)
    assert result.returncode == 0, result.stdout + result.stderr


def test_repo_alembic_tree_is_clean() -> None:
    """Pinning test: the CURRENT alembic/versions tree passes (modulo the
    grandfather list). A new migration reintroducing the foot-gun fails CI
    here AND the pre-commit hook."""
    result = _run_lint("--all")
    assert result.returncode == 0, result.stdout + result.stderr
