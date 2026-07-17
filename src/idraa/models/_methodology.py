"""Cross-entity methodology field constraints.
Extracted across overlay + calibration_override models + schemas
so the rule lives in one place."""

from typing import Final

METHODOLOGY_MIN_LENGTH: Final[int] = 20
"""Minimum non-whitespace character count for a methodology field.
Rationale: 20 chars enforces 'sentence-ish' content; rejects
single-word junk like 'updated' or 'TBD' that does not constitute
audit-grade provenance."""
