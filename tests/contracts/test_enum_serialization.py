# tests/contracts/test_enum_serialization.py
"""Regression test for the project-convention Enum column declaration.

Bug background: SQLAlchemy ``Enum(<EnumClass>, native_enum=False)`` defaults
to serializing enum NAMES. The Alembic CHECK constraints enforce VALUES.
Mismatch → IntegrityError on every form-submission insert against an
Alembic-migrated DB. The test suite masks this because tests use
``Base.metadata.create_all`` (NAME-based CHECK), not Alembic migrations.

PR ω fix: add ``values_callable=lambda x: [e.value for e in x]`` to every
Enum column so SA serializes by VALUE.

This test asserts that every Enum-typed mapped_column on every ORM model
has ``values_callable`` set. Adding a new Enum column without it = test
fails with a clear pointer to this regression.
"""

from __future__ import annotations

from sqlalchemy import Enum

from tests.contracts._registries import all_orm_entities


def test_every_enum_column_has_values_callable() -> None:
    """Every ``Enum(<EnumClass>, native_enum=False)`` column must set
    ``values_callable`` so SQLAlchemy serializes by VALUE, matching the
    Alembic CHECK-constraint convention.

    Failure means SA will write enum NAMES to the DB, which the Alembic
    CHECK constraint will reject as IntegrityError.
    """
    from typing import cast

    from sqlalchemy.inspection import inspect
    from sqlalchemy.orm import Mapper

    offenders: list[str] = []
    for entity_name, orm_class in all_orm_entities():
        mapper = cast("Mapper[type]", inspect(orm_class))
        for col in mapper.local_table.columns:
            col_type = col.type
            if isinstance(col_type, Enum):
                # Only relevant for Python-Enum-backed columns. String-based
                # Enums (with explicit value list) are fine without values_callable.
                if col_type.enum_class is None:
                    continue
                # values_callable is the public API surface that controls
                # NAME vs VALUE serialization for non-native enums.
                if col_type.values_callable is None:
                    offenders.append(
                        f"{entity_name}.{col.key} declared as "
                        f"Enum({col_type.enum_class.__name__}, ...) "
                        f"without values_callable. Add "
                        f"values_callable=lambda x: [e.value for e in x] "
                        f"to match Alembic CHECK convention."
                    )

    assert not offenders, (
        "Enum columns missing values_callable (would write NAMES, "
        "Alembic CHECK enforces VALUES — IntegrityError at runtime):\n  " + "\n  ".join(offenders)
    )
