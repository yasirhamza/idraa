"""Mixins cover UUID pk + timestamps + org column.

We declare ``_Dummy`` against a local ``DeclarativeBase`` rather than
``idraa.db.Base`` so the throwaway test table does not leak into the
application's metadata. If it did, other tests that call
``Base.metadata.create_all()`` would try to materialise ``_dummy_for_test``
with a FK to ``organizations`` (which only exists after Task 1.1.2) and fail
with ``NoReferencedTableError``. The plan's original snippet imports the real
``Base``; using a scoped base preserves the test's intent with no FK leakage.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from idraa.models.enums import UserRole
from idraa.models.mixins import IdMixin, OrgMixin, TimestampMixin


class _TestBase(DeclarativeBase):
    """Isolated declarative base for this module — keeps ``_Dummy`` out of
    ``idraa.db.Base.metadata``."""


class _Dummy(IdMixin, TimestampMixin, OrgMixin, _TestBase):
    __tablename__ = "_dummy_for_test"
    name: Mapped[str] = mapped_column()


def test_id_mixin_sets_uuid() -> None:
    d = _Dummy(name="x")
    assert isinstance(d.id, UUID)


def test_timestamps_default_to_now() -> None:
    d = _Dummy(name="x")
    assert isinstance(d.created_at, datetime)
    assert isinstance(d.updated_at, datetime)


def test_userrole_str_enum_values() -> None:
    assert UserRole.ADMIN.value == "admin"
    assert UserRole("analyst") is UserRole.ANALYST
