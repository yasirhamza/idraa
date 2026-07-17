"""Framework→FAIR-CAM crosswalk (P2a). Canonical authority data (NOT org-scoped):
the FAIR Institute NIST CSF 1.1 + CIS 8.0 → FAIR-CAM mappings. FrameworkControl =
one framework subcategory/safeguard; FrameworkControlFairCam = its FAIR-CAM
function links. Cited by the control library (P2b) to ground FAIR-CAM claims."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Uuid as UuidType
from sqlalchemy.orm import Mapped, mapped_column

from idraa.db import Base
from idraa.models.enums import FairCamSubFunction
from idraa.models.mixins import IdMixin


class FrameworkControl(IdMixin, Base):
    __tablename__ = "framework_controls"
    __table_args__ = (
        UniqueConstraint(
            "framework", "framework_version", "code", name="uq_framework_control_code"
        ),
    )
    framework: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    framework_version: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    security_function: Mapped[str | None] = mapped_column(String(128), nullable=True)
    citation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class FrameworkControlFairCam(IdMixin, Base):
    __tablename__ = "framework_control_faircam"
    __table_args__ = (
        UniqueConstraint(
            "framework_control_id",
            "fair_cam_function",
            name="uq_framework_control_faircam",
        ),
    )
    framework_control_id: Mapped[uuid.UUID] = mapped_column(
        UuidType(as_uuid=True),
        ForeignKey("framework_controls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fair_cam_function: Mapped[FairCamSubFunction] = mapped_column(
        SAEnum(
            FairCamSubFunction,
            native_enum=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    # Link-level provenance (#449): True for Idraa-added methodology links
    # (seed JSON riskflow_extension_functions), False for FAIR-Institute canon.
    is_extension: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
    )
