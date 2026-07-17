"""Pydantic schemas for SME directory CRUD. Per spec §7.5.

Sec-1 R1 mass-assignment defense: NO organization_id, created_by,
is_system_owned, created_via, archived_at, archived_by fields. All
server-derived."""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Arch-5 PR1 fix: avoid `EmailStr` (requires email-validator extra not in
# pyproject.toml). Use str + lightweight regex; project's lean-deps posture.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_email_str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not _EMAIL_RE.match(value):
        raise ValueError("invalid email format")
    return value


class SMECreate(_ForbidExtra):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    role_title: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        return _validate_email_str(v)


class SMERequest(_ForbidExtra):
    """Analyst-side; no notes."""

    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    role_title: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        return _validate_email_str(v)


class SMEUpdate(_ForbidExtra):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    role_title: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        return _validate_email_str(v)


class SubjectMatterExpertDropdownView(BaseModel):
    """Spec-9 PR1 fix: name aligned with spec §7.2 list_for_dropdown return type."""

    id: str
    name: str
    role_title: str | None
    is_system_owned: bool  # so the template can render MD-7 disclosure


class SMEDirectoryEntry(BaseModel):
    """JSON response for POST /scenarios/wizard/request-sme.

    Minimal shape the wizard combobox needs to push a newly-created SME
    into its client-side directory store without a page refresh. Excludes
    server-derived fields (created_at, organization_id, created_by, ...)
    and PII (email) by design.
    """

    id: UUID
    name: str
    role_title: str | None = None

    model_config = ConfigDict(from_attributes=True)
