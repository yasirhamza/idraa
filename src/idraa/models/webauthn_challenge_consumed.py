"""WebAuthnChallengeConsumed — single-use claim for a verified WebAuthn
challenge (advisory GHSA-46jj-823j-mjj9 B5).

Auth-layer table (no ``organization_id``), like ``LoginAttempt`` /
``AuthSession`` / the MFA tables: it is keyed on the challenge itself, not an
org-scoped business entity.

The UNIQUE key is ``challenge_digest`` ALONE, not ``(challenge_digest,
purpose)``. Login and registration share cookie ``rf_webauthn_challenge`` +
signing salt (``services/auth.py``), so digest-only uniqueness also blocks
cross-purpose reuse of the same signed cookie value; do not widen the key to
include ``purpose`` — see ``models.enums.WebAuthnChallengePurpose``.

``purpose`` is forensic-only metadata (which ceremony minted the challenge),
not part of the single-use claim's identity.

Written exclusively via ``services.webauthn_challenge.consume_challenge``
(atomic ``INSERT ... ON CONFLICT DO NOTHING``) — never construct/add this
model directly at a route call site.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.mixins import IdMixin


class WebAuthnChallengeConsumed(IdMixin, Base):
    __tablename__ = "webauthn_challenge_consumed"

    # unique implies an index — do NOT also pass index=True (redundant 2nd
    # index; mirrors LoginAttempt.source_key).
    challenge_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # consumed_at + WEBAUTHN_CHALLENGE_MAX_AGE + 60s — the retention sweep's
    # delete horizon (services/run_reaper.py::sweep_expired_webauthn_challenges).
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Guards audit_replay_once (services/webauthn_challenge.py): at most ONE
    # user.webauthn_challenge_replayed AuditLog row per challenge, mirroring
    # the self-limiting recovery_code_claim_lost pattern.
    replay_audited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )


__all__ = ["WebAuthnChallengeConsumed"]
