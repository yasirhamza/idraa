#!/usr/bin/env python3
"""Lint specs in docs/superpowers/specs/ for required scope-discipline sections.

Required sections that prevent the scope creep that killed v1/v2:

  ## Scope budget       — names a numeric target_task_count and review/timeline budget
  ## Scope drift log    — records every scope addition/cut/reframe vs the originating
                          brainstorm prompt, with a one-line justification per item

Both sections must exist AND have non-empty content (more than just the header).
This is a deterministic lint, run via pre-commit on any spec file change.

Legacy specs created before this lint was introduced are grandfathered via the
LEGACY_EXEMPT set below. New specs must comply — no exceptions.

Exit codes: 0 = all specs compliant, 1 = at least one violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "docs" / "superpowers" / "specs"

# Specs created before the scope-discipline lint was introduced (2026-04-28).
# Grandfathered explicitly. New specs MUST comply.
LEGACY_EXEMPT: frozenset[str] = frozenset(
    {
        "2026-04-25-calibration-data-framework-design.md",
        "2026-04-26-phase-1.3-scenarios-design.md",
        "2026-04-27-phase-1.4-monte-carlo-design.md",
    }
)

REQUIRED_SECTIONS: tuple[str, ...] = (
    "## Scope budget",
    "## Scope drift log",
)

# Heuristic for "non-empty content": at least one non-blank, non-header line
# between this section's heading and the next ## heading.
_MIN_CONTENT_CHARS = 40


def _section_content(text: str, heading: str) -> str | None:
    """Return content under heading (until next ##), or None if heading missing."""
    pattern = re.compile(
        rf"^{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(1).strip()


def lint_spec(path: Path) -> list[str]:
    """Return a list of human-readable error strings for path. Empty = compliant."""
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path.name}: cannot read file: {exc}"]

    for heading in REQUIRED_SECTIONS:
        content = _section_content(text, heading)
        if content is None:
            errors.append(f"{path.name}: missing required section '{heading}'")
            continue
        if len(content) < _MIN_CONTENT_CHARS:
            errors.append(
                f"{path.name}: section '{heading}' is empty or too short "
                f"(needs >= {_MIN_CONTENT_CHARS} chars of content)"
            )

    return errors


def find_target_specs(args: list[str]) -> list[Path]:
    """If args are given (e.g., from pre-commit), lint exactly those.
    Otherwise lint every spec under docs/superpowers/specs/."""
    if args:
        return [Path(a).resolve() for a in args]
    if not SPECS_DIR.is_dir():
        return []
    return sorted(SPECS_DIR.glob("*.md"))


def main(argv: list[str]) -> int:
    targets = find_target_specs(argv[1:])
    if not targets:
        return 0

    all_errors: list[str] = []
    for path in targets:
        if path.name in LEGACY_EXEMPT:
            continue
        if not path.is_file():
            continue
        errors = lint_spec(path)
        all_errors.extend(errors)

    if all_errors:
        print("Spec lint failed — scope-discipline sections missing or empty:\n")
        for e in all_errors:
            print(f"  {e}")
        print(
            "\nEvery spec in docs/superpowers/specs/ must have:\n"
            "  ## Scope budget       — numeric target_task_count + review/timeline budget\n"
            "  ## Scope drift log    — every scope addition/cut/reframe vs originating prompt\n"
            "\nThe purpose of these sections is to make scope creep visible at the spec gate.\n"
            "See docs/superpowers/templates/spec-template.md for the canonical template.\n"
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
