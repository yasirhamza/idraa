#!/usr/bin/env python3
# scripts/lint_alembic_uuids.py
"""Lint rule blocking the seed-UUID format foot-gun in alembic migrations.

The foot-gun (recurred 4x; repaired by migrations b3e9c1a47d52 +
e7d0c3a91f2b): raw-text seed INSERTs that bind ``str(uuid.uuid4())`` store
the 36-char hyphenated form, while the ORM ``Uuid`` type binds 32-char
no-hyphen hex — every id-based lookup then silently 404s.

Patterns flagged (per line, comments stripped):
- ``str(uuid.uuid4())`` / ``str(uuid4())`` — hyphenated string bind
- f-string interpolation ``{uuid.uuid4()}`` / ``{uuid4()}``
- hardcoded 36-char hyphenated UUID literals (``"xxxxxxxx-xxxx-..."``)

NOT flagged: bare ``uuid.uuid4()`` (no str()) — binding a UUID object
through an ORM-typed column (``op.bulk_insert`` on a table with
``Uuid(as_uuid=True)``) serializes correctly and is the legitimate pattern.
The prescribed string form is ``uuid.uuid4().hex``.

Opt-out: ``# uuid-format: ok — <reason>`` on the same line (em-dash or
``--``; non-empty reason required).

Grandfathered files: already-applied migrations are immutable — the
historical violators (since repaired by follow-up data migrations) and the
repair migrations whose docstrings quote the pattern are skipped by
basename. Do NOT add new files to this list; use ``.hex``.

Usage:
- ``python scripts/lint_alembic_uuids.py file1.py ...`` — scan named files
  (pre-commit ``pass_filenames`` mode).
- ``python scripts/lint_alembic_uuids.py --all`` — scan alembic/versions/*.py.

Exit code: 0 on no violations; 1 on any violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Already-applied (immutable) migrations containing the historical pattern,
# either as live code (since repaired by b3e9c1a47d52 / e7d0c3a91f2b data
# migrations) or quoted in docstrings. Frozen — never extend for new work.
_GRANDFATHERED: frozenset[str] = frozenset(
    {
        "a1b2c3d4e5f6_phase_1_5b_alpha_cfa.py",
        "c1d2e3f4a5b6_seed_library_entries.py",
        "d4f6a2b9c8e1_seed_control_library.py",
        "b3e9c1a47d52_fix_control_library_uuid_format.py",
        "e7d0c3a91f2b_fix_library_entry_uuid_format.py",
        "b8e0334b7f43_phase_1_5a_scenario_library_taxonomy.py",
        "60ff242180f6_seed_ciiib_expansion.py",
    }
)

# `# uuid-format: ok — <reason>` — em-dash (—) OR two-or-more hyphens (--)
# followed by a non-empty reason.
_OPT_OUT_RE = re.compile(r"#\s*uuid-format:\s*ok\s+(?:—|-{2,})\s+\S+", flags=re.UNICODE)
_OPT_OUT_MARKER_RE = re.compile(r"#\s*uuid-format:\s*ok")

_STR_UUID4_RE = re.compile(r"\bstr\(\s*(?:uuid\.)?uuid4\(\)\s*\)")
_FSTRING_UUID4_RE = re.compile(r"\{\s*(?:uuid\.)?uuid4\(\)\s*[}!:]")
_HYPHENATED_LITERAL_RE = re.compile(
    r"""['"][0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}['"]"""
)

_CHECKS: list[tuple[re.Pattern[str], str]] = [
    (_STR_UUID4_RE, "str(uuid4()) binds the hyphenated form"),
    (_FSTRING_UUID4_RE, "f-string uuid4() interpolates the hyphenated form"),
    (_HYPHENATED_LITERAL_RE, "hardcoded hyphenated UUID literal"),
]


def _strip_comment(line: str) -> str:
    """Naive comment strip — adequate for migration code; '#' inside string
    literals followed by a violation later on the same line is not a case the
    alembic tree exhibits."""
    return line.split("#", 1)[0]


def _scan_file(path: Path) -> list[str]:
    if path.name in _GRANDFATHERED:
        return []
    violations: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:  # unreadable file is itself an error
        return [f"{path}: unreadable ({exc})"]
    for lineno, line in enumerate(lines, start=1):
        if _OPT_OUT_RE.search(line):
            continue
        has_bare_marker = bool(_OPT_OUT_MARKER_RE.search(line))
        code = _strip_comment(line)
        for pattern, label in _CHECKS:
            if pattern.search(code):
                suffix = " (opt-out marker present but reason missing)" if has_bare_marker else ""
                violations.append(
                    f"{path}:{lineno}: {label} — ORM `Uuid` binds 32-char "
                    f"no-hyphen hex, so this silently 404s id lookups; use "
                    f"`uuid4().hex` or opt out with "
                    f"`# uuid-format: ok — <reason>`{suffix}"
                )
    return violations


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: lint_alembic_uuids.py <file.py> ... | --all", file=sys.stderr)
        return 2
    if argv == ["--all"]:
        repo_root = Path(__file__).resolve().parent.parent
        files = sorted((repo_root / "alembic" / "versions").glob("*.py"))
    else:
        files = [Path(a) for a in argv]

    all_violations: list[str] = []
    for f in files:
        all_violations.extend(_scan_file(f))

    for v in all_violations:
        print(v)
    return 1 if all_violations else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
