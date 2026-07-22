from __future__ import annotations

import uuid

from idraa.models.mfa import WebAuthnCredential
from idraa.services.mfa_enrollment import credential_views
from tests.contracts.helpers import assert_preserves_list_count


def _build(n: int) -> list[WebAuthnCredential]:
    return [
        WebAuthnCredential(
            user_id=uuid.uuid4(),
            credential_id=bytes([i]),
            public_key=b"k",
            nickname=f"key-{i}",
        )
        for i in range(n)
    ]


def test_credential_views_preserves_all() -> None:
    assert_preserves_list_count(credential_views, _build, n=3)
