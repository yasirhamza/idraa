"""Every FxRate ORM business column must be a constructor kwarg — guards against
a column added to the table but never settable from app code. Mirrors
tests/contracts/test_orm_sme_columns_subset_of_dto_fields.py."""

from __future__ import annotations

from sqlalchemy import inspect

from idraa.models.fx_rate import FxRate

# Columns set by mixins / DB, not by feature code.
_INTERNAL = {"id", "organization_id", "created_at", "updated_at"}


def test_every_business_column_is_a_constructor_kwarg() -> None:
    cols = {c.key for c in inspect(FxRate).columns} - _INTERNAL
    expected = {"code", "usd_rate", "as_of_date", "source", "is_active", "version"}
    assert cols == expected, f"FxRate columns drifted: {cols} != {expected}"
