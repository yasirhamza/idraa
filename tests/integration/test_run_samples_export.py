"""Integration tests for GET /runs/{run_id}/samples.csv.gz (#109).

Covers: correct index->scenario-id column mapping, both run_samples stores
(codec + legacy JSON), 410/404/401 status semantics, the per-process
concurrency guard (429 + Retry-After + idempotent release across the
completion/mid-stream-disconnect/error/finalizer paths), and the bulk-export
audit row.

``_seed_run_with_samples`` constructs the AGGREGATE run row directly:
conftest's ``seed_run_factory`` hardcodes ``run_type=RunType.SINGLE``, and
``seed_aggregate_run_factory`` seeds into ``seed_organization`` (a different
org than ``authed_admin``'s), so neither fits here. Mirrors the raw-UUID
foot-gun guard from tests/routes/test_run_delete.py (real ``uuid.UUID``
objects on ORM inserts, never ``str(uuid)``) and the ``organization_id``
NOT NULL requirement on ``RunSamples`` (OrgMixin).
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import numpy as np
import pytest
from fastapi.responses import StreamingResponse
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models._types import now_utc
from idraa.models.audit_log import AuditLog
from idraa.models.risk_analysis_run import RiskAnalysisRun, RunStatus, RunType
from idraa.models.run_samples import RunSamples
from idraa.models.user import User
from idraa.services.sample_codec import encode_sample_arrays

pytestmark = pytest.mark.asyncio


def _inline_agg_arrays(n: int = 5) -> dict[str, np.ndarray]:
    """The 8 aggregate-run array paths, mirroring tests/unit/test_sample_export.py's
    ``_agg_arrays`` shape (inlined per CLAUDE.md: tests must not import from
    other test modules)."""

    def a(seed: int) -> np.ndarray:
        return np.arange(n, dtype=np.float32) + seed

    return {
        "aggregate_with_controls": a(100),
        "aggregate_without_controls": a(200),
        "per_scenario/0/base_risk": a(0),
        "per_scenario/0/residual_risk": a(10),
        "per_scenario/1/base_risk": a(1),
        "per_scenario/1/residual_risk": a(11),
        "per_scenario/2/base_risk": a(2),
        "per_scenario/2/residual_risk": a(12),
    }


def _inline_agg_summary(s_ids: list[uuid.UUID]) -> dict[str, Any]:
    names = ["Ransomware", "OT Outage", "Insider Threat"]
    return {
        "per_scenario": [
            {"scenario_id": str(sid), "scenario_name": name}
            for sid, name in zip(s_ids, names, strict=True)
        ]
    }


async def _seed_run_with_samples(
    db_session: AsyncSession,
    org_id: uuid.UUID,
    *,
    arrays_override: dict[str, np.ndarray] | None = None,
    legacy_json: bool = False,
    skip_samples: bool = False,
) -> tuple[uuid.UUID, list[uuid.UUID], dict[str, np.ndarray]]:
    """Seed a COMPLETED AGGREGATE run + its RunSamples row directly.

    conftest's ``seed_run_factory`` hardcodes ``run_type=RunType.SINGLE``, so
    the AGGREGATE run row is constructed here directly (plan-gate SWE-I4).
    Returns ``(run_id, scenario_ids, arrays)``.
    """
    result = await db_session.execute(select(User).where(User.organization_id == org_id))
    user = result.scalars().first()
    assert user is not None, "org must have at least one user (authed_admin seeds one)"

    s_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    arrays = arrays_override if arrays_override is not None else _inline_agg_arrays()

    run = RiskAnalysisRun(
        id=uuid.uuid4(),
        organization_id=org_id,
        scenario_id=None,
        run_type=RunType.AGGREGATE,
        aggregate_scenario_ids=sorted(str(s) for s in s_ids),
        control_ids_used=[],
        mc_iterations=5,
        inputs_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        controls_snapshot=[],
        status=RunStatus.COMPLETED,
        created_by=user.id,
        simulation_results=_inline_agg_summary(s_ids),
        completed_at=now_utc(),
        random_seed=42,
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    if not skip_samples:
        samples_kwargs: dict[str, Any] = {"run_id": run.id, "organization_id": org_id}
        if legacy_json:
            samples_kwargs["arrays"] = {k: v.astype(np.float64).tolist() for k, v in arrays.items()}
        else:
            samples_kwargs["arrays_codec"] = encode_sample_arrays(dict(arrays))
        db_session.add(RunSamples(**samples_kwargs))
        await db_session.commit()

    return run.id, s_ids, arrays


async def test_export_streams_gzip_csv_with_correct_mapping(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    client, org_id = authed_admin
    run_id, s_ids, arrays = await _seed_run_with_samples(db_session, org_id)
    resp = await client.get(f"/runs/{run_id}/samples.csv.gz")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/gzip")
    assert f'filename="run-{run_id}-samples.csv.gz"' in resp.headers["content-disposition"]
    text = gzip.decompress(resp.content).decode("utf-8")
    rows = list(csv.reader(ln for ln in text.split("\r\n") if ln and not ln.startswith("#")))
    header, data = rows[0], rows[1:]
    assert header[0] == "iteration"
    assert f"scenario_{s_ids[1].hex}_base_risk" in header
    col = header.index(f"scenario_{s_ids[1].hex}_base_risk")
    src = arrays["per_scenario/1/base_risk"]
    assert [np.float32(r[col]) for r in data] == list(src)
    assert len(data) == len(src)
    # Provenance preamble present
    assert f"# run_id: {run_id}" in text
    assert "# schema: samples-export/1" in text


async def test_export_legacy_json_arrays_row(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    # Same seeding helper but store the arrays dict in RunSamples.arrays
    # (arrays_codec=None); expect identical CSV shape.
    client, org_id = authed_admin
    run_id, s_ids, arrays = await _seed_run_with_samples(db_session, org_id, legacy_json=True)
    resp = await client.get(f"/runs/{run_id}/samples.csv.gz")
    assert resp.status_code == 200
    text = gzip.decompress(resp.content).decode("utf-8")
    rows = list(csv.reader(ln for ln in text.split("\r\n") if ln and not ln.startswith("#")))
    header, data = rows[0], rows[1:]
    assert header[0] == "iteration"
    assert f"scenario_{s_ids[1].hex}_base_risk" in header
    col = header.index(f"scenario_{s_ids[1].hex}_base_risk")
    src = arrays["per_scenario/1/base_risk"]
    assert [np.float32(r[col]) for r in data] == list(src)
    assert len(data) == len(src)


async def test_export_purged_samples_410(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    # Seed run WITHOUT a RunSamples row.
    client, org_id = authed_admin
    run_id, _, _ = await _seed_run_with_samples(db_session, org_id, skip_samples=True)
    resp = await client.get(f"/runs/{run_id}/samples.csv.gz")
    assert resp.status_code == 410


async def test_export_unknown_run_404(authed_admin: tuple[AsyncClient, uuid.UUID]) -> None:
    client, _ = authed_admin
    resp = await client.get(f"/runs/{uuid.uuid4()}/samples.csv.gz")
    assert resp.status_code == 404


async def test_export_unauthenticated_rejected(client: AsyncClient) -> None:
    # Mirror test_controls_export_csv_requires_auth's accepted status set.
    fake_run_id = uuid.uuid4()
    resp = await client.get(f"/runs/{fake_run_id}/samples.csv.gz")
    assert resp.status_code in (302, 303, 307, 401, 403)
    assert resp.headers.get("content-type", "").startswith("application/gzip") is False


async def test_export_concurrency_guard_429(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    from idraa.routes import runs as runs_module

    client, org_id = authed_admin
    run_id, _, _ = await _seed_run_with_samples(db_session, org_id)
    acquired = runs_module._SAMPLES_EXPORT_GUARD.acquire(blocking=False)
    assert acquired
    try:
        resp = await client.get(f"/runs/{run_id}/samples.csv.gz")
        assert resp.status_code == 429
        assert resp.headers.get("retry-after")
    finally:
        runs_module._SAMPLES_EXPORT_GUARD.release()


async def test_export_slot_released_after_completion_and_idempotent(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    # Sec-I2: a completed export must free the single slot (idempotent under
    # both the generator-finally and BackgroundTask hooks firing), so a
    # follow-up export succeeds rather than 429ing.
    client, org_id = authed_admin
    run_id, _, _ = await _seed_run_with_samples(db_session, org_id)
    first = await client.get(f"/runs/{run_id}/samples.csv.gz")
    assert first.status_code == 200
    _ = first.content  # fully drain the stream
    second = await client.get(f"/runs/{run_id}/samples.csv.gz")
    assert second.status_code == 200  # 429 here means a leaked/double-counted permit


async def test_export_slot_released_on_midstream_close(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    # SWE3-2 / spec Testing 11: abandoning the stream mid-body must release
    # the slot via the generator-finally hook — a leak here bricks the export
    # surface (429s) until restart.
    client, org_id = authed_admin
    run_id, _, _ = await _seed_run_with_samples(db_session, org_id)
    async with client.stream("GET", f"/runs/{run_id}/samples.csv.gz") as resp:
        assert resp.status_code == 200
        async for _chunk in resp.aiter_bytes():
            break  # one chunk, then close mid-stream
    follow_up = await client.get(f"/runs/{run_id}/samples.csv.gz")
    assert follow_up.status_code == 200

    # NOTE (resolved empirically, not by assumption): httpx 0.28.1's
    # ASGITransport.handle_async_request (site-packages/httpx/_transports/
    # asgi.py) runs `await self.app(scope, receive, send)` to full
    # completion — accumulating every "http.response.body" chunk into
    # `body_parts` — BEFORE constructing and returning a Response at all.
    # `client.stream(...)` therefore never delivers a real mid-flight ASGI
    # disconnect through this transport: breaking out of `aiter_bytes()`
    # early only stops the *test* from reading further chunks of an
    # already-fully-materialized body. The `follow_up.status_code == 200`
    # assertion above is consistent with either release mechanism
    # (finally-on-disconnect OR finally-on-normal-completion +
    # BackgroundTask) and does not by itself prove the disconnect path.
    # Per spec Testing item 11's already-named substitution, drive the
    # generator-finally directly at the ASGI level: call the route function,
    # pull exactly one chunk through its StreamingResponse.body_iterator,
    # then .aclose() it (the real signal Starlette sends the body generator
    # on a genuine client disconnect), and assert the module semaphore is
    # free again afterward.
    from starlette.requests import Request

    from idraa.routes import runs as runs_module

    result = await db_session.execute(select(User).where(User.organization_id == org_id))
    user = result.scalars().first()
    assert user is not None
    run_id2, _, _ = await _seed_run_with_samples(db_session, org_id)

    request = Request(scope={"type": "http", "client": None, "headers": []})
    response = await runs_module.get_run_samples_csv_gz(
        request=request, run_id=run_id2, db=db_session, user=user
    )
    assert isinstance(response, StreamingResponse)
    # The slot is held by this in-flight (not-yet-closed) response.
    assert runs_module._SAMPLES_EXPORT_GUARD.acquire(blocking=False) is False

    body_iter = response.body_iterator
    assert isinstance(body_iter, AsyncGenerator)
    await body_iter.__anext__()  # pull exactly one chunk
    await body_iter.aclose()  # the real mid-stream-disconnect signal

    freed = runs_module._SAMPLES_EXPORT_GUARD.acquire(blocking=False)
    assert freed, "mid-stream close did not release the export concurrency slot"
    if freed:
        runs_module._SAMPLES_EXPORT_GUARD.release()


async def test_export_error_path_releases_slot(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    # SWE3-4: a 500 (unknown array path -> build_export_columns ValueError)
    # must exit through the except-BaseException release; the next export
    # must get 200, not 429.
    client, org_id = authed_admin
    bad_run_id, _, _ = await _seed_run_with_samples(
        db_session, org_id, arrays_override={"mystery_path": np.ones(4, dtype=np.float32)}
    )
    # NOTE (resolved empirically, not by assumption): the app DOES register
    # a catch-all `app.add_exception_handler(Exception, _server_error_handler)`
    # (src/idraa/app.py), but Starlette's ServerErrorMiddleware — which is
    # where that handler ends up wired (Starlette pulls the `Exception`/500
    # handler OUT of the per-type dict and installs it as ServerError
    # Middleware's own `handler=`, per starlette/middleware/errors.py) —
    # sends the 500 response AND THEN UNCONDITIONALLY re-raises the original
    # exception ("We always continue to raise the exception. This allows
    # servers to log the error, or allows test clients to optionally raise
    # the error within the test case." — starlette/middleware/errors.py).
    # httpx's ASGITransport defaults `raise_app_exceptions=True` (the
    # `client` fixture never overrides it), so that re-raised ValueError
    # propagates all the way out of `await client.get(...)` as a raised
    # exception rather than a 500 Response — confirmed by an initial run of
    # this test raising ValueError instead of getting a Response object.
    with pytest.raises(ValueError, match="unrecognised sample array paths"):
        await client.get(f"/runs/{bad_run_id}/samples.csv.gz")
    good_run_id, _, _ = await _seed_run_with_samples(db_session, org_id)
    follow_up = await client.get(f"/runs/{good_run_id}/samples.csv.gz")
    assert follow_up.status_code == 200


async def test_export_writes_bulk_export_audit_row(
    authed_admin: tuple[AsyncClient, uuid.UUID], db_session: AsyncSession
) -> None:
    # After a 200 export: one AuditLog row, action "run_samples.export",
    # changes include fmt csv.gz + filters.run_id. Mirror the audit-row
    # assertions used by the other export tests in tests/integration/
    # test_bulk_export_audit.py.
    client, org_id = authed_admin
    run_id, _, _ = await _seed_run_with_samples(db_session, org_id)
    resp = await client.get(f"/runs/{run_id}/samples.csv.gz")
    assert resp.status_code == 200
    _ = resp.content

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == "run_samples.export")
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.changes["format"] == [None, "csv.gz"]
    assert row.changes["filters"] == [None, {"run_id": str(run_id)}]
    count = row.changes["count"]
    assert isinstance(count, list)
    assert count[0] is None and isinstance(count[1], int) and count[1] >= 0
    assert row.user_id is not None
    assert row.entity_id == row.organization_id
