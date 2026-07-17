"""Reference-driven coverage: covered ∩ reference over reference.

The ONE coverage primitive for the app (control frameworks, FAIR-CAM domains,
scenario library, future MITRE #475). Pure and reference-agnostic — callers
pass the reference (denominator) and covered (numerator) sets, both sourced
from DATA (enums / seeded tables / curated library), never hardcoded here.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class CoverageResult:
    covered_count: int
    reference_count: int
    ratio: float
    missing: list[Hashable]  # reference items not covered — the actionable gap
    present: list[Hashable]  # reference items that ARE covered


def coverage(reference: Iterable[Hashable], covered: Iterable[Hashable]) -> CoverageResult:
    ref: list[Hashable] = []
    seen: set[Hashable] = set()
    for item in reference:  # preserve first-seen order, dedup
        if item not in seen:
            seen.add(item)
            ref.append(item)
    cov = set(covered)
    present = [x for x in ref if x in cov]
    missing = [x for x in ref if x not in cov]
    n = len(ref)
    return CoverageResult(
        covered_count=len(present),
        reference_count=n,
        ratio=(len(present) / n) if n else 0.0,
        missing=missing,
        present=present,
    )
