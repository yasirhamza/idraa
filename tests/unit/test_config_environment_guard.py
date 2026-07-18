"""Guard: ``Settings`` must refuse to boot in non-dev envs with weak secrets.

Failure mode we are defending against: someone ships the app with the
checked-in placeholder ``session_secret="change-me-in-production"`` by
forgetting to set ``SESSION_SECRET`` in the prod env. Any signed cookie
(session, CSRF, remember-me) would then be forgeable by anyone who can
read the default from the repo.
"""

from __future__ import annotations

import pytest

from idraa.config import Settings


def test_dev_default_secret_rejected() -> None:
    """``dev`` must also reject the checked-in placeholder.

    Closes the "ENVIRONMENT unset in prod" blind spot: an operator who
    forgets to set ``ENVIRONMENT=prod`` falls back to ``dev`` (the default),
    and the old guard only fired in non-dev envs — so the placeholder would
    have been accepted in a production deployment. Now ``dev`` + default
    secret raises, forcing contributors to set a non-default via ``.env``.
    """
    with pytest.raises(ValueError, match=r"SESSION_SECRET"):
        Settings(environment="dev", session_secret="change-me-in-production")


def test_dev_non_default_secret_ok() -> None:
    """``dev`` with any other 16+ char secret boots fine — one-time cost."""
    s = Settings(environment="dev", session_secret="x" * 16)
    assert s.environment == "dev"
    assert s.session_secret == "x" * 16


def test_test_default_secret_ok() -> None:
    """``test`` env legitimately uses disposable defaults."""
    s = Settings(environment="test", session_secret="change-me-in-production")
    assert s.environment == "test"


def test_prod_default_secret_rejected() -> None:
    with pytest.raises(ValueError, match=r"SESSION_SECRET"):
        Settings(environment="prod", session_secret="change-me-in-production")


def test_prod_short_secret_rejected() -> None:
    """Pydantic's ``min_length=16`` covers dev floor; prod requires >=32."""
    with pytest.raises(ValueError, match=r"SESSION_SECRET"):
        Settings(environment="prod", session_secret="x" * 16)


def test_prod_long_secret_ok() -> None:
    s = Settings(environment="prod", session_secret="x" * 32)
    assert s.environment == "prod"


def test_prod_error_message_is_actionable() -> None:
    """Error must name the env var so an operator knows what to set."""
    with pytest.raises(ValueError) as exc_info:
        Settings(environment="prod", session_secret="change-me-in-production")
    msg = str(exc_info.value)
    assert "SESSION_SECRET" in msg, f"missing env-var name: {msg}"


def test_environment_default_is_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unconfigured env defaults to dev — not prod, not anything else.

    We unset ``ENVIRONMENT`` here because ``tests/conftest.py`` sets
    ``ENVIRONMENT=test`` for the whole suite (so the module-level
    ``app = create_app()`` can boot with the default secret); for this
    specific test we want to observe the ``Settings`` field default, not
    the test harness's override.

    Pass a non-default secret so the tightened guard (dev no longer accepts
    the placeholder) doesn't fire — we only want to exercise the
    environment-default here, not the secret.
    """
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    s = Settings(session_secret="x" * 16)
    assert s.environment == "dev"


def test_quantile_fit_budget_raised_in_test_env() -> None:
    """The suite-wide scipy.optimize deadline must stay raised (issue #36).

    ``tests/conftest.py`` sets ``QUANTILE_FIT_WALL_CLOCK_MS=5000`` before
    importing ``idraa`` because the prod default (500ms) is a real-time
    deadline that suite-accumulated load can bust, non-deterministically
    failing wizard-finalize tests that assert locking semantics, not
    optimizer latency. If this assertion fires, the conftest override was
    removed or reordered below the ``idraa`` import — restore it rather
    than deleting this test, or the full-suite flake returns.
    """
    from idraa.config import get_settings

    assert get_settings().quantile_fit_wall_clock_ms >= 5000

    # The raise is test-harness-only: the shipped default stays 500ms.
    field = Settings.model_fields["quantile_fit_wall_clock_ms"]
    assert field.default == 500
