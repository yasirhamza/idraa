"""Seed-validation schemas for the ATT&CK catalog + curated mappings (#475).

Validate ``data/seed_attack_catalog.json`` / ``data/seed_attack_*_mappings.json``
rows at seed-build and migration time. SEED-VALIDATION schemas only, NOT
contract DTOs — do NOT register in any DTO/contract snapshot registry.

Provenance gate (mirrors ControlLibraryAssignmentSeed): ``provenance='cited'``
requires ≥1 non-whitespace citation. The ATT&CK technique page itself is
catalog attribution, not grounding for a mapping claim — a cited mapping must
trace to a primary incident/report source evidencing the technique behavior.
Loss-magnitude citations (IRIS loss tables) are NEVER valid technique
grounding (Meth-B1) — that rule is semantic and enforced by the methodology
review, not this schema; the schema enforces the syntactic guards:
cited ⇒ citations, expert-estimate rationale must not claim "cited",
rationale bounded.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AttackTacticSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["enterprise", "ics", "atlas"]
    tactic_id: str = Field(pattern=r"^(TA\d{4}|AML\.TA\d{4})$")
    shortname: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    display_order: int = Field(ge=0)
    # Sec2-N1: constrain at the source — a later template WILL want to link
    # these (the #349 linkify-allowlist tripwire exists for exactly this).
    url: str = Field(pattern=r"^https://(attack|atlas)\.mitre\.org/")


class AttackTechniqueSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["enterprise", "ics", "atlas"]
    # Parent techniques only (PR 1 convention, held for ATLAS too — #482): the
    # builder skips sub-techniques; this pattern rejects T####.### AND
    # AML.T####.### sub-technique forms outright.
    technique_id: str = Field(pattern=r"^(T\d{4}|AML\.T\d{4})$")
    name: str = Field(min_length=1)
    description: str | None = None
    tactics: list[str] = Field(min_length=1)
    url: str = Field(pattern=r"^https://(attack|atlas)\.mitre\.org/")  # Sec2-N1
    citation: dict[str, Any]


class EntryAttackMappingSeed(BaseModel):
    """One curated (library entry → technique) claim.

    ``entry_slug`` references ``scenario_library_entries.slug``; the seeding
    migration resolves it to the entry's (id, MAX(version)) at migration time
    and fails loud if the slug is missing.
    """

    model_config = ConfigDict(extra="forbid")

    entry_slug: str = Field(min_length=1)
    domain: Literal["enterprise", "ics", "atlas"]
    technique_id: str = Field(pattern=r"^(T\d{4}|AML\.T\d{4})$")
    rationale: str = Field(max_length=2000)
    provenance: Literal["cited", "expert-estimate"]
    citations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _guards(self) -> EntryAttackMappingSeed:
        if not self.rationale.strip():
            raise ValueError("rationale must be non-empty")
        if self.provenance == "cited":
            if not any(c.strip() for c in self.citations):
                raise ValueError("provenance='cited' requires at least one non-whitespace citation")
        elif re.search(r"\bcited\b", self.rationale, re.IGNORECASE):
            # Meth-B1(c): an expert-estimate rationale must not describe the
            # claim as cited — provenance labels live in `provenance`, only.
            raise ValueError("expert-estimate rationale must not claim cited status")
        return self
