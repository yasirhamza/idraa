"""SMEEstimateRow XOR identity validator.

Companion to the DB-layer test_sse_xor_constraint contract — the same
invariant enforced at the schema layer so Pydantic rejects malformed
payloads before they hit the database.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from idraa.schemas.wizard_step3 import SMEEstimateRow


def test_both_null_rejected() -> None:
    with pytest.raises(ValidationError, match="exactly one of sme_id or sme_name"):
        SMEEstimateRow(sme_id=None, sme_name=None, low=0.1, high=0.5)


def test_both_set_rejected() -> None:
    with pytest.raises(ValidationError, match="exactly one of sme_id or sme_name"):
        SMEEstimateRow(sme_id=uuid4(), sme_name="Alice", low=0.1, high=0.5)


def test_only_sme_id_accepted() -> None:
    row = SMEEstimateRow(sme_id=uuid4(), low=0.1, high=0.5)
    assert row.sme_name is None


def test_only_sme_name_accepted() -> None:
    row = SMEEstimateRow(sme_name="Alice Chen", low=0.1, high=0.5)
    assert row.sme_id is None


def test_empty_sme_name_rejected() -> None:
    with pytest.raises(ValidationError):
        SMEEstimateRow(sme_name="", low=0.1, high=0.5)


def test_sme_name_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        SMEEstimateRow(sme_name="A" * 201, low=0.1, high=0.5)
