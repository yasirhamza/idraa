"""#327 — DB-write boundary rejects non-finite floats in JSON columns.

The default ``json.dumps`` emits the non-standard tokens ``Infinity`` /
``NaN`` for non-finite floats, which corrupt durable JSON blobs
(``risk_analysis_run.simulation_results``, ``run_samples.arrays``) and break
strict consumers (``JSON.parse("Infinity")`` throws in the browser). This is
the #306→#307 failure-mode class.

``strict_json_dumps`` (wired as the engine's ``json_serializer``) makes ANY
non-finite-to-DB write fail loudly at flush/commit instead of silently
storing corruption. These tests exercise the guard through the same fixture
engine the rest of the suite uses (tests/conftest.py wires the serializer
identically to src/idraa/db.py:get_engine).
"""

from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.db import strict_json_dumps
from idraa.models.audit_log import AuditLog


def test_strict_json_dumps_rejects_inf() -> None:
    with pytest.raises(ValueError):
        strict_json_dumps({"ale_mean": float("inf")})


def test_strict_json_dumps_rejects_nan() -> None:
    with pytest.raises(ValueError):
        strict_json_dumps({"ale_mean": float("nan")})


def test_strict_json_dumps_passes_finite_payloads() -> None:
    payload = {"ale_mean": 1.5e6, "nested": {"samples": [0.0, -2.5, 3.14]}}
    out = strict_json_dumps(payload)
    assert "Infinity" not in out and "NaN" not in out
    import json

    assert json.loads(out) == payload


@pytest.mark.asyncio
async def test_json_column_write_with_inf_raises_at_flush(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: object,
) -> None:
    """A non-finite value in a JSON column must raise at write time, never
    durably store the non-standard ``Infinity`` token."""
    row = AuditLog(
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        entity_type="test",
        entity_id=uuid.uuid4(),
        user_id=seed_user.id,  # type: ignore[attr-defined]
        action="test.nonfinite_guard",
        changes={"ale": [None, float("inf")]},
    )
    db_session.add(row)
    # SQLAlchemy wraps the serializer's ValueError in a StatementError at the
    # ORM execution layer; the original ValueError rides along as __cause__.
    with pytest.raises(StatementError) as excinfo:
        await db_session.flush()
    assert isinstance(excinfo.value.orig, ValueError)
    await db_session.rollback()


@pytest.mark.asyncio
async def test_json_column_finite_payload_round_trips(
    db_session: AsyncSession,
    seed_organization: object,
    seed_user: object,
) -> None:
    """Acceptance #327: existing finite payloads round-trip unchanged."""
    entity_id = uuid.uuid4()
    payload = {"ale": [None, 1.25e6], "note": "finite ok", "n": 100_000}
    row = AuditLog(
        organization_id=seed_organization.id,  # type: ignore[attr-defined]
        entity_type="test",
        entity_id=entity_id,
        user_id=seed_user.id,  # type: ignore[attr-defined]
        action="test.finite_roundtrip",
        changes=payload,
    )
    db_session.add(row)
    await db_session.flush()
    db_session.expire(row)

    fetched = (
        await db_session.execute(select(AuditLog).where(AuditLog.entity_id == entity_id))
    ).scalar_one()
    assert fetched.changes == payload
    assert all(
        not isinstance(v, float) or math.isfinite(v)
        for v in fetched.changes["ale"]  # type: ignore[union-attr]
        if v is not None
    )
