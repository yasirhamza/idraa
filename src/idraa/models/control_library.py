"""Control library catalog (P2b). Canonical (NOT org-scoped) catalog of concrete
control types, cross-mapped to frameworks + FAIR-CAM functions, with reference
effectiveness defaults. Mirrors ScenarioLibraryEntry (composite (id, version) PK,
immutable versioned snapshots) and ControlFunctionAssignment (the assignment child
rows). Adopted into editable org Controls via the adopt clone-snapshot (P2b §6)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Enum,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.enums import ControlType, FairCamSubFunction
from idraa.models.mixins import IdMixin, TimestampMixin


class ControlLibraryEntry(TimestampMixin, Base):
    __tablename__ = "control_library_entries"
    __table_args__ = (
        PrimaryKeyConstraint("id", "version", name="pk_control_library_entries"),
        UniqueConstraint("slug", "version", name="uq_control_library_entry_slug_version"),
        Index("ix_control_library_entry_status", "status"),
        Index("ix_control_library_entry_control_type", "control_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UuidType(as_uuid=True), default=uuid.uuid4, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    control_type: Mapped[ControlType] = mapped_column(
        Enum(ControlType, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    reference_annual_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    nist_csf_subcategories: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    cis_safeguards: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    iso_27001_controls: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    compliance_mappings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    applicable_industries: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    applicable_org_sizes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source_citations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(
            "draft",
            "published",
            "deprecated",
            native_enum=False,
            create_constraint=True,
            name="control_library_entry_status",
        ),
        default="draft",
        nullable=False,
    )
    row_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("id", uuid.uuid4())
        super().__init__(**kwargs)


class ControlLibraryEntryAssignment(IdMixin, TimestampMixin, Base):
    __tablename__ = "control_library_entry_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["library_entry_id", "library_entry_version"],
            ["control_library_entries.id", "control_library_entries.version"],
            ondelete="CASCADE",
            name="fk_clea_entry",
        ),
        UniqueConstraint(
            "library_entry_id",
            "library_entry_version",
            "sub_function",
            name="uq_clea_entry_sub_function",
        ),
        Index("ix_clea_entry", "library_entry_id", "library_entry_version"),
    )

    library_entry_id: Mapped[uuid.UUID] = mapped_column(UuidType(as_uuid=True), nullable=False)
    library_entry_version: Mapped[int] = mapped_column(Integer, nullable=False)
    sub_function: Mapped[FairCamSubFunction] = mapped_column(
        Enum(FairCamSubFunction, native_enum=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    capability_default: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_default: Mapped[float] = mapped_column(Float, nullable=False)
    reliability_default: Mapped[float] = mapped_column(Float, nullable=False)
    capability_provenance: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # set iff capability_default set
    capability_citations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    coverage_provenance: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="expert-estimate"
    )
    coverage_citations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    reliability_provenance: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="expert-estimate"
    )
    reliability_citations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
