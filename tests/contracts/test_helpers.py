# tests/contracts/test_helpers.py
"""Direct tests for tests/contracts/helpers.py.

These run BEFORE any parameterized test consumes the helpers — they are
the TDD anchors that prove the helpers are correct in isolation.
"""

from __future__ import annotations

import pytest

# ---- assert_orm_dto_field_sync — three direction checks ----
# Use real SQLAlchemy mapped classes for these tests so the helper's
# inspection path is the SAME as production.
from pydantic import BaseModel
from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tests.contracts.helpers import (
    assert_orm_dto_field_sync,
    assert_preserves_list_count,
    snapshot_orm_shape,
    snapshot_pydantic_shape,
)


class _TestBase(DeclarativeBase):
    pass


class _OrmAB(_TestBase):
    __tablename__ = "_test_orm_ab"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    a: Mapped[str] = mapped_column(String(10))
    b: Mapped[str] = mapped_column(String(10))


class _DtoAB(BaseModel):
    a: str
    b: str


class _DtoOnlyA(BaseModel):
    a: str


class _DtoExtraField(BaseModel):
    a: str
    b: str
    c: str  # not on the ORM


def test_field_sync_passes_when_orm_dto_aligned_via_allowlist() -> None:
    """ORM has {id, a, b}; DTO has {a, b}; allowlist = {id}. All three checks pass."""
    assert_orm_dto_field_sync(
        orm_class=_OrmAB,
        dto_class=_DtoAB,
        allowlist={"id"},
    )


def test_field_sync_fails_when_orm_column_missing_from_dto_and_allowlist() -> None:
    """ORM has {id, a, b}; DTO has {a}; allowlist = {id}. Direction-1 check fails on 'b'."""
    with pytest.raises(AssertionError, match=r"ORM column.*\bb\b.*not in DTO.*allowlist"):
        assert_orm_dto_field_sync(
            orm_class=_OrmAB,
            dto_class=_DtoOnlyA,
            allowlist={"id"},
        )


def test_field_sync_fails_when_dto_field_missing_from_orm() -> None:
    """ORM has {id, a, b}; DTO has {a, b, c}; 'c' is on DTO but not ORM. Direction-2 fails."""
    with pytest.raises(AssertionError, match=r"DTO field.*\bc\b.*no matching ORM column"):
        assert_orm_dto_field_sync(
            orm_class=_OrmAB,
            dto_class=_DtoExtraField,
            allowlist={"id"},
        )


def test_field_sync_passes_when_dto_only_field_is_allowlisted() -> None:
    """ORM has {id, a, b}; DTO has {a, b, c}; dto_only_allowlist = {c}.

    Direction-2 accepts c via dto_only_allowlist; defense-in-depth does NOT
    fire on c because dto_only_allowlist entries are deliberately DTO-only.
    """
    assert_orm_dto_field_sync(
        orm_class=_OrmAB,
        dto_class=_DtoExtraField,
        allowlist={"id"},
        dto_only_allowlist={"c"},
    )


def test_field_sync_fails_when_allowlist_has_stale_entry() -> None:
    """ORM has {id, a, b}; DTO has {a, b}; allowlist = {id, ghost_column}. Defense-in-depth fails."""
    with pytest.raises(AssertionError, match=r"Allowlist entr.*ghost_column.*not.*ORM"):
        assert_orm_dto_field_sync(
            orm_class=_OrmAB,
            dto_class=_DtoAB,
            allowlist={"id", "ghost_column"},
        )


# ---- snapshot_orm_shape ----


class _OrmComplete(_TestBase):
    """Spans every shape field the snapshot must capture."""

    __tablename__ = "_test_orm_complete"
    __table_args__ = (Index("ix_test_orm_complete_name", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str | None] = mapped_column(String(20), nullable=True, default="")


def test_snapshot_orm_shape_captures_table_name_and_columns() -> None:
    shape = snapshot_orm_shape(_OrmComplete)
    assert shape["table_name"] == "_test_orm_complete"
    assert set(shape["columns"].keys()) == {"id", "org_id", "name", "label"}


def test_snapshot_orm_shape_captures_column_attributes() -> None:
    shape = snapshot_orm_shape(_OrmComplete)
    assert shape["columns"]["id"]["primary_key"] is True
    assert shape["columns"]["id"]["nullable"] is False
    assert shape["columns"]["org_id"]["indexed"] is True
    assert shape["columns"]["org_id"]["nullable"] is False
    assert shape["columns"]["name"]["nullable"] is False
    assert shape["columns"]["name"]["indexed"] is False  # explicit Index, not column-level
    assert shape["columns"]["label"]["nullable"] is True
    assert shape["columns"]["label"]["has_default"] is True


def test_snapshot_orm_shape_lists_indexes_alphabetically() -> None:
    """Determinism: index list must be sorted so JSON output is stable."""
    shape = snapshot_orm_shape(_OrmComplete)
    indexes = shape["indexes"]
    assert indexes == sorted(indexes)
    assert "ix_test_orm_complete_name" in indexes


def test_snapshot_orm_shape_columns_dict_keys_are_alphabetic() -> None:
    """Determinism: column dict iteration order must be stable across runs."""
    shape = snapshot_orm_shape(_OrmComplete)
    keys = list(shape["columns"].keys())
    assert keys == sorted(keys)


# ---- snapshot_pydantic_shape ----


class _DtoComplete(BaseModel):
    name: str
    description: str | None = None
    count: int = 0


def test_snapshot_pydantic_shape_captures_schema_name_and_fields() -> None:
    shape = snapshot_pydantic_shape(_DtoComplete)
    assert shape["schema_name"] == "_DtoComplete"
    assert set(shape["fields"].keys()) == {"name", "description", "count"}


def test_snapshot_pydantic_shape_captures_field_attributes() -> None:
    shape = snapshot_pydantic_shape(_DtoComplete)
    assert shape["fields"]["name"]["optional"] is False
    assert shape["fields"]["name"]["has_default"] is False
    assert shape["fields"]["description"]["optional"] is True
    assert shape["fields"]["description"]["has_default"] is True
    assert shape["fields"]["count"]["optional"] is False
    assert shape["fields"]["count"]["has_default"] is True


def test_snapshot_pydantic_shape_field_keys_are_alphabetic() -> None:
    """Determinism: field dict iteration order must be stable."""
    shape = snapshot_pydantic_shape(_DtoComplete)
    keys = list(shape["fields"].keys())
    assert keys == sorted(keys)


# ---- assert_preserves_list_count ----


def _identity(xs: list[int]) -> list[int]:
    return xs


def _drop_first(xs: list[int]) -> list[int]:
    """Simulates the κ-class bug — silently drops the first element."""
    return xs[1:]


def _take_first(xs: list[int]) -> list[int]:
    """Simulates the literal κ bug — returns only the first element."""
    return [xs[0]] if xs else []


def _double_each(xs: list[int]) -> list[int]:
    """Fan-out: produces 2 outputs per input."""
    return [x for x in xs for _ in range(2)]


def test_preserves_list_count_eq_passes_on_identity() -> None:
    assert_preserves_list_count(
        func=_identity,
        build_input=lambda n: list(range(n)),
        n=3,
        expected_len_relation="eq",
    )


def test_preserves_list_count_eq_fails_on_dropping_function() -> None:
    """The κ-class bug: function takes 3, returns 1. eq mode catches it."""
    with pytest.raises(AssertionError, match=r"expected list length 3.*got 1"):
        assert_preserves_list_count(
            func=_take_first,
            build_input=lambda n: list(range(n)),
            n=3,
            expected_len_relation="eq",
        )


def test_preserves_list_count_ge_accepts_fan_out() -> None:
    """ge mode: 3 in, 6 out is fine — count is ≥ n."""
    assert_preserves_list_count(
        func=_double_each,
        build_input=lambda n: list(range(n)),
        n=3,
        expected_len_relation="ge",
    )


def test_preserves_list_count_ge_rejects_drop() -> None:
    with pytest.raises(AssertionError, match=r"expected list length >= 3.*got 2"):
        assert_preserves_list_count(
            func=_drop_first,
            build_input=lambda n: list(range(n)),
            n=3,
            expected_len_relation="ge",
        )


def test_preserves_list_count_le_accepts_filter() -> None:
    """le mode: 3 in, 2 out is fine — count is ≤ n (filter-style adapter)."""
    assert_preserves_list_count(
        func=_drop_first,  # 3 → 2, satisfies ≤ 3
        build_input=lambda n: list(range(n)),
        n=3,
        expected_len_relation="le",
    )


def test_preserves_list_count_le_rejects_fan_out() -> None:
    with pytest.raises(AssertionError, match=r"expected list length <= 3.*got 6"):
        assert_preserves_list_count(
            func=_double_each,
            build_input=lambda n: list(range(n)),
            n=3,
            expected_len_relation="le",
        )


def test_preserves_list_count_default_mode_is_eq() -> None:
    """Default mode is 'eq' — the κ-class case. Verify by omitting the kwarg."""
    assert_preserves_list_count(
        func=_identity,
        build_input=lambda n: list(range(n)),
    )


def test_preserves_list_count_rejects_non_list_output() -> None:
    """The helper applies only to list-shaped functions; non-list output raises.

    A buggy adapter could return a tuple, dict, or None instead of a list.
    The helper catches this misuse with a clear AssertionError naming the
    offending return type.
    """

    def returns_tuple(xs: list[int]) -> tuple[int, ...]:
        return tuple(xs)

    with pytest.raises(AssertionError, match=r"returned tuple, expected list"):
        assert_preserves_list_count(
            func=returns_tuple,  # type: ignore[arg-type]
            build_input=lambda n: list(range(n)),
            n=3,
            expected_len_relation="eq",
        )
