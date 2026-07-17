"""Helpers that derive a stable identity per estimate row.

When a row has sme_id (FK), identity = that UUID.
When a row has sme_name (free-text), identity = uuid5(NAMESPACE_DNS, "freetext:" + name.casefold()).

The synth UUID approach keeps build_scenario_payload's sidecar `sme_ids`
field and AuditClampEvent.sme_id typed as UUID without changing their
shapes, so downstream snapshot/sidecar consumers don't need to change.
"""

from __future__ import annotations

from uuid import NAMESPACE_DNS, UUID, uuid5

from idraa.services.wizard_finalize import row_identity_uuid


def test_sme_id_row_returns_its_uuid() -> None:
    u = UUID("12345678-1234-5678-1234-567812345678")
    assert row_identity_uuid({"sme_id": u, "sme_name": None}) == u


def test_sme_id_row_returns_uuid_when_id_is_string() -> None:
    s = "12345678-1234-5678-1234-567812345678"
    assert row_identity_uuid({"sme_id": s, "sme_name": None}) == UUID(s)


def test_freetext_row_returns_uuid5_of_casefolded_name() -> None:
    expected = uuid5(NAMESPACE_DNS, "freetext:alice chen")
    assert row_identity_uuid({"sme_id": None, "sme_name": "Alice Chen"}) == expected


def test_freetext_row_is_case_insensitive() -> None:
    u1 = row_identity_uuid({"sme_id": None, "sme_name": "Alice"})
    u2 = row_identity_uuid({"sme_id": None, "sme_name": "ALICE"})
    u3 = row_identity_uuid({"sme_id": None, "sme_name": "alice"})
    assert u1 == u2 == u3


def test_freetext_row_distinguishes_different_names() -> None:
    u1 = row_identity_uuid({"sme_id": None, "sme_name": "Alice"})
    u2 = row_identity_uuid({"sme_id": None, "sme_name": "Bob"})
    assert u1 != u2
