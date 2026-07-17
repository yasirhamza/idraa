# tests/contracts/test_field_sync.py
"""Parameterized ORM↔DTO field-sync test (PR ρ A2).

Covers all entity pairs declared in pyproject.toml's
[tool.idraa.contracts.field_sync.*] blocks. One test case per
(orm, dto) combination; each case exercises the three-direction sync
helper (assert_orm_dto_field_sync) — three internal checks per case is
intentional: a real failure fires once per drift event, not three times,
keeping the test report signal tight.

Replaces the one-off tests/unit/test_orm_dto_field_sync.py. Adding a new
entity = adding a new [tool.idraa.contracts.field_sync.<name>] block;
the parameterization picks it up automatically.
"""

from __future__ import annotations

import pytest

from tests.contracts._registries import field_sync_pairs
from tests.contracts.helpers import assert_orm_dto_field_sync

_PAIRS = field_sync_pairs()


@pytest.mark.parametrize(
    ("orm_class", "dto_class", "allowlist", "dto_only_allowlist"),
    [(orm, dto, allowlist, dto_only) for _, orm, dto, allowlist, dto_only in _PAIRS],
    ids=[case_id for case_id, *_ in _PAIRS],
)
def test_orm_dto_field_sync(
    orm_class: type,
    dto_class: type,
    allowlist: set[str],
    dto_only_allowlist: frozenset[str],
) -> None:
    """Three-way field sync per (orm, dto) pair.

    On failure, the parametrize id (e.g., 'scenario:ScenarioForm') tells
    you which entity drifted. The helper's error message tells you which
    direction (ORM→DTO miss, DTO→ORM miss, or stale-allowlist).
    """
    assert_orm_dto_field_sync(orm_class, dto_class, allowlist, dto_only_allowlist)
