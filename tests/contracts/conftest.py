# tests/contracts/conftest.py
"""Pytest fixtures for PR ρ schema-consistency tests.

Plain helper functions (registries) live in tests/contracts/_registries.py.
This file is for fixtures only — it is the syrupy-extension configuration
surface plus the ``snapshot_orm`` / ``snapshot_pydantic`` fixtures consumed
by tests/contracts/test_schema_snapshots.py.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.single_file import SingleFileSnapshotExtension

from tests.contracts._registries import _project_root


class _OrmJsonExtension(SingleFileSnapshotExtension):
    """Write ORM snapshots to tests/contracts/snapshots/orm/<ClassName>.json as JSON.

    Overrides:
    - ``_file_extension``: sets file suffix to ``json``.
    - ``dirname``: points to ``tests/contracts/snapshots/orm/``.
    - ``get_snapshot_name``: returns the raw index string (the class name) so
      the file is named ``Scenario.json`` rather than
      ``test_orm_shape_matches_snapshot[Scenario].json``.
    - ``serialize``: JSON-encodes the dict and returns bytes (binary write mode).
    """

    # syrupy >=5 renamed the class attribute from ``_file_extension`` to
    # ``file_extension`` (used by ``get_location`` to build the snapshot path).
    # Setting the old name silently fell back to the default "raw" extension,
    # so the stored ``<ClassName>.json`` files were never found.
    file_extension = "json"

    @classmethod
    def dirname(cls, *, test_location: Any) -> str:
        return str(_project_root() / "tests" / "contracts" / "snapshots" / "orm")

    @classmethod
    def get_snapshot_name(cls, *, test_location: Any, index: Any = 0) -> str:
        """Return the raw index string so files are named <ClassName>.json."""
        if isinstance(index, str):
            return index
        return str(index)

    def serialize(
        self,
        data: Any,
        *,
        exclude: Any | None = None,
        include: Any | None = None,
        matcher: Any | None = None,
    ) -> bytes:
        # Trailing newline ensures end-of-file-fixer pre-commit hook does not
        # modify the generated file (which would break the byte-level comparison
        # on the next pytest run).
        return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


class _PydanticJsonExtension(SingleFileSnapshotExtension):
    """Write Pydantic snapshots to tests/contracts/snapshots/pydantic/<ClassName>.json.

    Same override strategy as ``_OrmJsonExtension`` — see that class for
    rationale.
    """

    # syrupy >=5 renamed the class attribute from ``_file_extension`` to
    # ``file_extension`` (used by ``get_location`` to build the snapshot path).
    # Setting the old name silently fell back to the default "raw" extension,
    # so the stored ``<ClassName>.json`` files were never found.
    file_extension = "json"

    @classmethod
    def dirname(cls, *, test_location: Any) -> str:
        return str(_project_root() / "tests" / "contracts" / "snapshots" / "pydantic")

    @classmethod
    def get_snapshot_name(cls, *, test_location: Any, index: Any = 0) -> str:
        """Return the raw index string so files are named <ClassName>.json."""
        if isinstance(index, str):
            return index
        return str(index)

    def serialize(
        self,
        data: Any,
        *,
        exclude: Any | None = None,
        include: Any | None = None,
        matcher: Any | None = None,
    ) -> bytes:
        # Trailing newline ensures end-of-file-fixer pre-commit hook does not
        # modify the generated file (which would break the byte-level comparison
        # on the next pytest run).
        return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


@pytest.fixture
def snapshot_orm(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(_OrmJsonExtension)


@pytest.fixture
def snapshot_pydantic(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(_PydanticJsonExtension)
