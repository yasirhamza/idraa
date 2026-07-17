# tests/contracts/test_schema_snapshots.py
"""Schema snapshot tripwire (PR ρ A3).

For each ORM table and Pydantic schema, snapshot a deterministic dict of
its structural shape (names + types + nullability + defaults + indexes
for ORM; names + types + optional + defaults for Pydantic). Mismatch
fails the test. Snapshot regeneration: pytest --snapshot-update.

The snapshot diff in a PR is the reviewer's "did you mean to do this?"
checkpoint for any schema reshape.
"""

from __future__ import annotations

import pytest
from syrupy.assertion import SnapshotAssertion

from tests.contracts._registries import all_orm_entities, all_pydantic_schemas
from tests.contracts.helpers import snapshot_orm_shape, snapshot_pydantic_shape

_ORM = all_orm_entities()
_PYD = all_pydantic_schemas()


@pytest.mark.parametrize(
    "orm_class",
    [cls for _, cls in _ORM],
    ids=[entity_id for entity_id, _ in _ORM],
)
def test_orm_shape_matches_snapshot(orm_class: type, snapshot_orm: SnapshotAssertion) -> None:
    """ORM shape must match snapshots/orm/<ClassName>.json.

    Regenerate with: ``pytest tests/contracts/test_schema_snapshots.py --snapshot-update``
    Reviewer must approve every regenerated snapshot diff.
    """
    shape = snapshot_orm_shape(orm_class)
    assert shape == snapshot_orm(name=orm_class.__name__)


@pytest.mark.parametrize(
    "schema_class",
    [cls for _, cls in _PYD],
    ids=[schema_id for schema_id, _ in _PYD],
)
def test_pydantic_shape_matches_snapshot(
    schema_class: type, snapshot_pydantic: SnapshotAssertion
) -> None:
    """Pydantic schema shape must match snapshots/pydantic/<ClassName>.json.

    Regenerate with: ``pytest tests/contracts/test_schema_snapshots.py --snapshot-update``
    """
    shape = snapshot_pydantic_shape(schema_class)
    assert shape == snapshot_pydantic(name=schema_class.__name__)
