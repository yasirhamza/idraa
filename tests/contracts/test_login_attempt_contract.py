from sqlalchemy import inspect

from idraa.models.login_attempt import LoginAttempt
from idraa.models.mfa import UserTotp


def test_login_attempt_columns():
    cols = {c.name for c in inspect(LoginAttempt).columns}
    assert {
        "id",
        "source_key",
        "failed_count",
        "window_started_at",
        "blocked_until",
        "created_at",
        "updated_at",
    } <= cols
    sk = LoginAttempt.__table__.c.source_key
    assert sk.unique is True and isinstance(sk.type.length, int) and sk.type.length >= 64


def test_user_totp_has_last_used_step():
    assert "last_used_step" in {c.name for c in inspect(UserTotp).columns}
