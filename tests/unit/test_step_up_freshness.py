from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

import idraa.config as config
from idraa.models.session import AuthSession
from idraa.services.auth import is_step_up_fresh


def _sess(reauth: datetime | None) -> AuthSession:
    now = datetime.now(UTC)
    return AuthSession(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=14),
        reauthenticated_at=reauth,
    )


def test_fresh_within_window() -> None:
    assert is_step_up_fresh(_sess(datetime.now(UTC) - timedelta(seconds=30))) is True


def test_stale_beyond_window() -> None:
    assert is_step_up_fresh(_sess(datetime.now(UTC) - timedelta(seconds=601))) is False


def test_none_is_stale() -> None:
    # Pre-P2 session rows have no reauthenticated_at — fail closed.
    assert is_step_up_fresh(_sess(None)) is False


def test_naive_datetime_reattaches_utc() -> None:
    # aiosqlite strips tzinfo on cross-connection reads; a naive value is
    # known-UTC (create_session's invariant) and must not raise.
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30)
    assert is_step_up_fresh(_sess(naive)) is True


def test_zero_disables_step_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_STEP_UP_MAX_AGE_SECONDS", "0")
    config.reset_for_tests()
    try:
        assert is_step_up_fresh(_sess(None)) is True
    finally:
        monkeypatch.delenv("AUTH_STEP_UP_MAX_AGE_SECONDS")
        config.reset_for_tests()


def test_model_has_reauthenticated_at_column() -> None:
    # Pins column existence only (AttributeError if dropped). The actual
    # create_session stamping is covered by the DB-backed integration tests.
    assert AuthSession.reauthenticated_at is not None
