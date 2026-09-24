from sqlalchemy import inspect

from idraa.models.webauthn_challenge_consumed import WebAuthnChallengeConsumed


def test_webauthn_challenge_consumed_columns():
    cols = {c.name for c in inspect(WebAuthnChallengeConsumed).columns}
    assert {
        "id",
        "challenge_digest",
        "purpose",
        "consumed_at",
        "expires_at",
        "replay_audited",
    } <= cols
    digest = WebAuthnChallengeConsumed.__table__.c.challenge_digest
    assert (
        digest.unique is True and isinstance(digest.type.length, int) and digest.type.length == 64
    )
    purpose = WebAuthnChallengeConsumed.__table__.c.purpose
    assert isinstance(purpose.type.length, int) and purpose.type.length == 16
