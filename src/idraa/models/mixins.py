"""Shared SQLAlchemy mixins: UUID pk, timestamps, organization FK.

Python-side ``default=`` on ``mapped_column`` only materialises at flush, not at
``__init__`` — which means a freshly constructed in-memory instance would have
``id=None`` and ``created_at=None`` until it hits the DB. That breaks any code
(tests, services) that reads those fields before persistence. We close the gap
with a class-level ``init`` event listener wired on via the ``instrument_class``
mapper event, so every subclass that inherits from IdMixin / TimestampMixin
gets its defaults populated at construction time too.

The ``server_default=func.now()`` on timestamp columns stays as the DB-side
safety net for inserts that bypass the ORM (raw SQL, bulk operations).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Uuid, event, func
from sqlalchemy.orm import Mapped, Mapper, declared_attr, mapped_column

from idraa.models._types import now_utc


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        server_default=func.now(),
        nullable=False,
    )


class OrgMixin:
    @declared_attr
    @classmethod
    def organization_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            Uuid(as_uuid=True),
            ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )


def _populate_defaults_on_init(target: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    """Mirror ``default=`` Python callables into ``__init__`` kwargs.

    SQLAlchemy's Python-side column defaults only fire at flush time; this hook
    makes them visible on in-memory instances too so ``Model(...).id`` and
    ``.created_at`` are populated the moment construction returns.
    """
    cls = type(target)
    if issubclass(cls, IdMixin) and kwargs.get("id") is None:
        kwargs["id"] = uuid.uuid4()
    if issubclass(cls, TimestampMixin):
        now = now_utc()
        kwargs.setdefault("created_at", now)
        kwargs.setdefault("updated_at", now)


@event.listens_for(Mapper, "instrument_class")
def _register_init_listener(mapper: Mapper[Any], cls: type) -> None:
    """Attach the defaults-populating init hook to every mixed-in mapped class.

    ``instrument_class`` fires once per class at mapping time (before any
    instance is constructed), which avoids the ``deque mutated during
    iteration`` bug that hits if we register from ``mapper_configured``.
    Propagating via ``propagate=True`` on a Mapper-level ``init`` listener is
    also broken in SA 2.0.49 (``ClassManager.subclass_managers()`` signature
    mismatch), so per-class registration is the reliable path.
    """
    if issubclass(cls, (IdMixin, TimestampMixin)):
        event.listen(cls, "init", _populate_defaults_on_init)
