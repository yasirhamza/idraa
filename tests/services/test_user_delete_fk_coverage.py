"""#133 guard: every users.id FK that would block a hard delete is pre-checked.

A foreign key to ``users.id`` that neither nulls out nor cascades (no
``ondelete``, or explicit RESTRICT / NO ACTION) blocks the DELETE: deleting a
referenced user raises IntegrityError (a 500). ``delete_user`` must refuse
with the 409 "deactivate instead" first, so each such column has to be in
``services.users._HISTORY_COLUMNS``. A new table with a NO-ACTION user FK
fails here until it is added there (or given SET NULL / CASCADE).
"""

from __future__ import annotations

import importlib
import pkgutil

import idraa.models
from idraa.models.user import User
from idraa.services.users import _HISTORY_COLUMNS


def _user_fks() -> list[tuple[str, str, str | None]]:
    for mod in pkgutil.iter_modules(idraa.models.__path__):
        importlib.import_module(f"idraa.models.{mod.name}")
    return [
        (table.name, fk.parent.name, fk.ondelete)
        for table in User.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.column.table.name == "users"
    ]


def test_every_no_action_user_fk_is_pre_checked() -> None:
    fks = _user_fks()
    no_action = {
        (t, c) for t, c, ondelete in fks if (ondelete or "").upper() not in {"SET NULL", "CASCADE"}
    }
    assert no_action, "introspection found no NO-ACTION user FKs; guard would be vacuous"
    covered = {(model.__table__.name, col.key) for model, col, _ in _HISTORY_COLUMNS}
    missing = sorted(no_action - covered)
    assert not missing, f"users.id FKs with no ondelete missing from _HISTORY_COLUMNS: {missing}"
