"""Data-contract iteration test for the qualitative register converter
(PR rho rule; epic #34 P1b Task 5, plan-gate Spec-N1).

``QualitativeConverterService.convert`` is a ``list[BoundRow] ->
ConversionReport`` adapter whose output is spread across FOUR buckets
(created / parked / skipped_duplicates / errors) instead of a single list.
The N>=3 adapter-iteration contract here is that every row lands in
EXACTLY ONE bucket — a future ``[0]`` / ``[-1]`` / "just handle the first
match" optimization on any bucket would silently drop rows from the
others without this test catching it.

Homed here (not tests/unit/) per the plan's binding Task 5 amendment:
"The N>=3 adapter-iteration test is homed in
tests/contracts/test_qualitative_converter_iteration.py per the
data-contract policy."
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.enums import ThreatCategory
from idraa.models.qualitative_mapping import QualitativeMappingBand
from idraa.services.qualitative_converter import BoundRow, QualitativeConverterService


async def _seed_band(
    db_session: AsyncSession,
    *,
    kind: str,
    label: str,
    low: float,
    mode: float,
    high: float,
) -> None:
    db_session.add(
        QualitativeMappingBand(
            kind=kind,
            label=label,
            low=low,
            mode=mode,
            high=high,
            sort_order=1,
            derivation="unit-test canonical band, not a real citation",
            version=1,
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_five_rows_across_all_four_buckets_all_preserved(
    db_session: AsyncSession,
    seed_org_user: Callable[..., Awaitable[tuple[object, object]]],
) -> None:
    org, user = await seed_org_user(db_session)
    await _seed_band(db_session, kind="frequency", label="moderate", low=1.0, mode=3.2, high=10.0)
    await _seed_band(
        db_session,
        kind="magnitude",
        label="high",
        low=1_000_000.0,
        mode=3_200_000.0,
        high=10_000_000.0,
    )

    def _raw() -> dict[str, str]:
        return {"likelihood": "Likely", "impact": "High", "category": "Phishing"}

    rows = [
        # 1, 2: created.
        BoundRow(
            source_row=1,
            title="Row One",
            description=None,
            owner=None,
            likelihood_label="moderate",
            magnitude_label="high",
            category=ThreatCategory.SOCIAL_ENGINEERING,
            raw=_raw(),
        ),
        BoundRow(
            source_row=2,
            title="Row Two",
            description=None,
            owner=None,
            likelihood_label="moderate",
            magnitude_label="high",
            category=ThreatCategory.RANSOMWARE,
            raw=_raw(),
        ),
        # 3: parked.
        BoundRow(
            source_row=3,
            title="Row Three",
            description=None,
            owner=None,
            likelihood_label="moderate",
            magnitude_label="high",
            category=None,
            raw=_raw(),
        ),
        # 4: skip-duplicate (same name as row 1).
        BoundRow(
            source_row=4,
            title="Row One",
            description=None,
            owner=None,
            likelihood_label="moderate",
            magnitude_label="high",
            category=ThreatCategory.MALWARE,
            raw=_raw(),
        ),
        # 5: RowError (unknown band label).
        BoundRow(
            source_row=5,
            title="Row Five",
            description=None,
            owner=None,
            likelihood_label="unknown_label",
            magnitude_label="high",
            category=ThreatCategory.MALWARE,
            raw=_raw(),
        ),
    ]

    report = await QualitativeConverterService(db_session).convert(
        organization_id=org.id, user=user, source_file="register.xlsx", rows=rows
    )

    total = (
        len(report.created)
        + len(report.parked)
        + len(report.skipped_duplicates)
        + len(report.errors)
    )
    assert total == 5, "every input row must land in exactly one output bucket"

    assert {c.source_row for c in report.created} == {1, 2}
    assert [(p.source_row, p.reason) for p in report.parked] == [(3, "category")]
    assert {s.source_row for s in report.skipped_duplicates} == {4}
    assert {e.source_row for e in report.errors} == {5}
