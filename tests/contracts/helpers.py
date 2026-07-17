# tests/contracts/helpers.py
"""Reusable assertion helpers for schema consistency hygiene (PR ρ).

The four helpers are:

- :func:`assert_orm_dto_field_sync` — three-way structural sync check.
- :func:`snapshot_orm_shape` — introspect an ORM table into a deterministic dict.
- :func:`snapshot_pydantic_shape` — introspect a Pydantic schema into a deterministic dict.
- :func:`assert_preserves_list_count` — assert a list-shaped function preserves cardinality.

See ``docs/plans/2026-05-04-pr-rho-schema-consistency-hygiene-design.md`` for
the rationale and decision log.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Literal, cast

from pydantic import BaseModel

_MEMORY_ADDRESS_RE = re.compile(r" at 0x[0-9a-fA-F]+")


def assert_orm_dto_field_sync(
    orm_class: type,
    dto_class: type[BaseModel],
    allowlist: set[str],
    dto_only_allowlist: frozenset[str] | set[str] = frozenset(),
) -> None:
    """Assert ORM↔DTO field sync via three independent checks.

    Direction 1: every ORM column appears in the DTO field set or the allowlist.
    Direction 2: every DTO field appears in the ORM column set or dto_only_allowlist.
    Defense-in-depth: every ``allowlist`` entry is a real ORM column.

    Each check fails with a clear message naming the offending field(s).

    :param orm_class: SQLAlchemy mapped class (uses ``inspect(cls).columns``).
    :param dto_class: Pydantic ``BaseModel`` subclass (uses ``model_fields``).
    :param allowlist: set of ORM column names that legitimately don't appear
        on the DTO (e.g., ``id``, ``created_at``, ``organization_id``).
    :param dto_only_allowlist: set of DTO field names that legitimately don't
        appear as ORM columns (e.g., ``ScenarioForm.overlay_tags`` — a user-input
        list of tag strings consumed during scenario creation; the resolved data
        lives in ``Scenario.overlay_pins`` under a different shape). Entries in
        this set are NOT validated against ORM columns by the defense-in-depth
        check.
    """
    from sqlalchemy.inspection import inspect
    from sqlalchemy.orm import Mapper

    insp_orm: Mapper[Any] = cast("Mapper[Any]", inspect(orm_class))
    orm_columns = {col.key for col in insp_orm.columns}
    dto_fields = set(dto_class.model_fields.keys())
    dto_only_set = set(dto_only_allowlist)

    # Direction 1: ORM columns missing from DTO and not allowlisted.
    missing_in_dto = orm_columns - dto_fields - allowlist
    assert not missing_in_dto, (
        f"ORM column(s) {sorted(missing_in_dto)} not in DTO {dto_class.__name__} "
        f"and not in allowlist. Either add to DTO or extend allowlist with "
        f"a reason."
    )

    # Direction 2: DTO fields with no matching ORM column and not dto_only_allowlisted.
    missing_in_orm = dto_fields - orm_columns - dto_only_set
    assert not missing_in_orm, (
        f"DTO field(s) {sorted(missing_in_orm)} on {dto_class.__name__} "
        f"have no matching ORM column on {orm_class.__name__} and are not in "
        f"dto_only_allowlist. Either add the column to ORM, remove from DTO, "
        f"or extend dto_only_allowlist with a reason if the field is "
        f"intentionally DTO-only."
    )

    # Defense-in-depth: stale allowlist entries (ORM-side allowlist only).
    # dto_only_allowlist entries are deliberately not ORM columns and are
    # intentionally not validated here.
    stale_allowlist = allowlist - orm_columns
    assert not stale_allowlist, (
        f"Allowlist entr{'ies' if len(stale_allowlist) > 1 else 'y'} "
        f"{sorted(stale_allowlist)} are not actual ORM columns on "
        f"{orm_class.__name__}. Remove from allowlist."
    )


def snapshot_orm_shape(orm_class: type) -> dict[str, Any]:
    """Return a deterministic dict capturing an ORM table's structural shape.

    Output structure::

        {
          "table_name": str,
          "columns": {
            <col_name>: {
              "type": str,                # str(col.type) — e.g. "VARCHAR(50)", "UUID"
              "nullable": bool,
              "has_default": bool,        # True if column has a Python or SQL default
              "indexed": bool,            # column-level index=True
              "primary_key": bool,
              "foreign_key": bool,
            }
          },
          "indexes": [str]                # sorted; table-level Index() names
        }

    Iteration order is alphabetic on column names + alphabetic on index names
    for stable JSON serialization across runs.

    :param orm_class: SQLAlchemy mapped class (subclass of ``DeclarativeBase``).
    """
    from sqlalchemy.inspection import inspect
    from sqlalchemy.orm import Mapper
    from sqlalchemy.sql.schema import Table

    insp: Mapper[Any] = cast("Mapper[Any]", inspect(orm_class))
    table: Table = cast("Table", insp.local_table)

    columns: dict[str, dict[str, Any]] = {}
    for col in sorted(table.columns, key=lambda c: c.key):
        columns[col.key] = {
            "type": str(col.type),
            "nullable": bool(col.nullable),
            "has_default": col.default is not None or col.server_default is not None,
            "indexed": bool(col.index) if col.index is not None else False,
            "primary_key": bool(col.primary_key),
            "foreign_key": len(col.foreign_keys) > 0,
        }

    indexes = sorted(idx.name for idx in table.indexes if idx.name is not None)

    return {
        "table_name": table.name,
        "columns": columns,
        "indexes": indexes,
    }


def snapshot_pydantic_shape(dto_class: type[BaseModel]) -> dict[str, Any]:
    """Return a deterministic dict capturing a Pydantic schema's structural shape.

    Output structure::

        {
          "schema_name": str,
          "fields": {
            <field_name>: {
              "type": str,            # str of the field's annotation
              "optional": bool,       # True if Optional / Union[..., None]
              "has_default": bool,    # True if FieldInfo.is_required() is False
            }
          }
        }

    Iteration order is alphabetic on field names for stable JSON output.

    :param dto_class: Pydantic ``BaseModel`` subclass.
    """
    fields: dict[str, dict[str, Any]] = {}
    for field_name, field_info in sorted(dto_class.model_fields.items()):
        annotation = field_info.annotation
        type_str = str(annotation) if annotation is not None else "Any"
        # Strip "typing." prefix for readability without losing precision.
        type_str = type_str.replace("typing.", "")
        # Strip memory addresses (e.g. "<function foo at 0x7f1234>") so that
        # type strings containing discriminator callables are deterministic
        # across process restarts.
        type_str = _MEMORY_ADDRESS_RE.sub("", type_str)
        # Optional / Union[X, None] detection.
        optional = ("None" in type_str) or (
            not field_info.is_required() and field_info.default is None
        )
        has_default = not field_info.is_required()
        fields[field_name] = {
            "type": type_str,
            "optional": optional,
            "has_default": has_default,
        }

    return {
        "schema_name": dto_class.__name__,
        "fields": fields,
    }


def assert_preserves_list_count(
    func: Callable[..., list[Any]],
    build_input: Callable[[int], Any],
    n: int = 3,
    expected_len_relation: Literal["eq", "ge", "le"] = "eq",
) -> None:
    """Assert that a list-shaped function preserves cardinality at N≥3.

    Three modes:
    - ``"eq"`` (default): exactly n inputs in, n outputs out. The κ-class case.
    - ``"ge"``: ≥ n outputs (fan-out adapters where one input may produce ≥1 outputs).
    - ``"le"``: ≤ n outputs (filter-style adapters that may drop entries).

    :param func: callable under test; its result must be a list.
    :param build_input: callable taking ``n`` and returning whatever ``func``
        accepts as input. Allows fixture-style construction of arbitrary
        valid inputs without baking ORM/DB knowledge into the helper.
    :param n: input cardinality (default 3 — see PR ρ design § feedback memo
        rationale: N=3 is the smallest count that surfaces ``[0]`` / ``[-1]`` / ``[mid]``
        bugs).
    :param expected_len_relation: ``"eq" | "ge" | "le"``.
    """
    input_value = build_input(n)
    output = func(input_value)

    if not isinstance(output, list):
        raise AssertionError(
            f"function {func.__name__} returned {type(output).__name__}, "
            f"expected list. Helper applies only to list-shaped functions."
        )

    actual = len(output)
    if expected_len_relation == "eq":
        assert actual == n, (
            f"function {func.__name__}: expected list length {n}, got {actual}. "
            f"This is the κ-class silent-data-loss pattern."
        )
    elif expected_len_relation == "ge":
        assert actual >= n, (
            f"function {func.__name__}: expected list length >= {n}, got {actual}. "
            f"Fan-out adapter dropped entries."
        )
    elif expected_len_relation == "le":
        assert actual <= n, (
            f"function {func.__name__}: expected list length <= {n}, got {actual}. "
            f"Filter-style adapter produced more entries than inputs."
        )
    else:  # pragma: no cover — Literal type narrows this out at static-check time
        raise ValueError(f"unknown relation {expected_len_relation!r}")
