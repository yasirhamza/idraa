"""Seed-validation schema for data/seed_threat_communities.json (spec §4.1)."""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

import idraa
from idraa.models.threat_community import CANONICAL_THREAT_COMMUNITY_SLUGS

SEED_PATH: Path = (
    Path(idraa.__file__).resolve().parent.parent.parent / "data" / "seed_threat_communities.json"
)

_TEXT_FIELDS = (
    "summary",
    "motive",
    "primary_intent",
    "sponsorship",
    "preferred_target_characteristics",
    "preferred_targets",
    "capability",
    "personal_risk_tolerance",
    "collateral_damage_concern",
    "threat_event_definition",
    "tef_basis",
    "rationale",
)
# Canonical control-naive clauses. A substring check is a TRIPWIRE against a curator forgetting
# the rule; the PR-gate methodology review is the semantic check (a sentence can contain the
# clause and mean the opposite).
_ACCIDENTAL_CLAUSE = "nothing intervening"
_MALICIOUS_CLAUSE = "whether or not your controls stop it"


class LandmarkTriple(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low: float
    mode: float
    high: float
    basis_class: Literal["cited", "derived", "convention"]
    derivation: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _ordered_finite(self) -> LandmarkTriple:
        if not all(math.isfinite(v) for v in (self.low, self.mode, self.high)):
            raise ValueError("landmark values must be finite")
        if not (self.low <= self.mode <= self.high) or not (self.low < self.high):
            raise ValueError("landmark must satisfy low <= mode <= high and low < high")
        if not self.derivation.strip():
            raise ValueError("derivation must be non-blank")
        return self


class TefLandmark(LandmarkTriple):
    """TEF landmark. IRIS/DBIR-class sources count incidents or losses, which sit AFTER
    controls; a TEF is attempts (before controls). A non-attempt source must therefore be
    divided by ``assumed_conversion`` — the CONTROLLED-WORLD attempt→source-event conversion
    rate matching ``source_event_level`` (attempt→incident or attempt→loss event; the
    ``TEF = LEF / vuln`` translation of fair-cam-methodology.md:145-170), a curator
    convention that already includes every control effect and is NOT the inherent
    Vulnerability of register A2 — and is then ``derived`` by construction. The derivation
    must also attribute an all-actor statistic to this community (share applied to λ). The
    conversion is THIS community's rate; a value shared across communities must be declared
    as such in the rationale with its bias direction. A corpus total (events per dataset)
    is not a rate — only a per-organisation count/rate may skip the p->λ step."""

    source_event_level: Literal["attempt", "incident", "loss_event"]  # required, no default
    assumed_conversion: float | None = None

    @model_validator(mode="after")
    def _event_level(self) -> TefLandmark:
        if self.source_event_level == "attempt":
            if self.assumed_conversion is not None:
                raise ValueError("assumed_conversion is only for incident/loss_event sources")
        else:
            if self.assumed_conversion is None or not (0.0 < self.assumed_conversion <= 1.0):
                raise ValueError("a non-attempt TEF source needs assumed_conversion in (0, 1]")
            if self.basis_class != "derived":
                raise ValueError(
                    "a converted TEF landmark is 'derived' (a cited base statistic carried "
                    "through stated conversion steps)"
                )
        return self


class TcapLandmark(LandmarkTriple):
    """TCap landmark: a percentile rank. No event-level fields (extra='forbid' on the base)."""


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=256)
    url: str = Field(
        pattern=r"^https://", max_length=512
    )  # #349 tripwire, at the source; render-time gate in Task 10
    locator: str | None = Field(default=None, max_length=128)
    edition_year: int | None = Field(default=None, ge=1990, le=2100)


class ReferenceOrg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: str = Field(min_length=1, max_length=32)
    sector: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _known_values(self) -> ReferenceOrg:
        from idraa.models.enums import IndustryType, OrganizationSize

        if self.size not in {m.value for m in OrganizationSize}:
            raise ValueError(f"unknown organization size {self.size!r}")
        if self.sector != "neutral" and self.sector not in {m.value for m in IndustryType}:
            raise ValueError(f"unknown sector {self.sector!r}")
        return self


class ThreatCommunitySeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    summary: str
    origin: Literal["internal", "external"]
    intent: Literal["malicious", "non_malicious"]
    motive: str
    primary_intent: str
    sponsorship: str
    preferred_target_characteristics: str
    preferred_targets: str
    capability: str
    personal_risk_tolerance: str
    collateral_damage_concern: str
    threat_event_definition: str
    tef_basis: str
    reference_org: ReferenceOrg
    tef_landmark: TefLandmark
    tcap_landmark: TcapLandmark | None = None
    rationale: str
    citations: list[Citation] = Field(min_length=1)
    reviewed_at: dt.date

    @model_validator(mode="after")
    def _guards(self) -> ThreatCommunitySeed:
        if self.slug not in CANONICAL_THREAT_COMMUNITY_SLUGS:
            raise ValueError(f"unknown canonical slug {self.slug!r}")
        for f in _TEXT_FIELDS:
            if not getattr(self, f).strip():
                raise ValueError(f"{f} must be non-blank")
        if self.tef_landmark.low <= 0:
            raise ValueError("tef_landmark.low must be > 0 (events/yr)")
        if (self.tcap_landmark is None) != (self.intent == "non_malicious"):
            raise ValueError("tcap_landmark must be null exactly for non-malicious communities")
        if self.tcap_landmark is not None:
            c = self.tcap_landmark
            if c.low < 0 or c.high > 100:
                raise ValueError("tcap_landmark must lie within [0, 100]")
            if c.high <= 1:
                raise ValueError(
                    "tcap_landmark is a percentile rank (0-100), not a probability"
                )  # ASCII hyphen: ruff RUF001
            if c.basis_class == "derived":
                raise ValueError("tcap_landmark.basis_class must be 'cited' or 'convention'")
        for tri in (self.tef_landmark, self.tcap_landmark):
            if (
                tri is not None
                and tri.basis_class in ("cited", "derived")
                and not any(c.locator for c in self.citations)
            ):
                raise ValueError(
                    f"a {tri.basis_class} landmark needs at least one citation with a locator"
                )
        # Control-naive counting rule (tripwire), checked on threat_event_definition ALONE (it is
        # the field that reaches the wizard's TEF tooltip). Event-level rules live on TefLandmark.
        clause = _ACCIDENTAL_CLAUSE if self.intent == "non_malicious" else _MALICIOUS_CLAUSE
        if clause not in self.threat_event_definition.lower():
            raise ValueError(
                f"threat_event_definition must contain the control-naive clause {clause!r}"
            )
        return self


def load_threat_community_seed(path: Path | None = None) -> list[ThreatCommunitySeed]:
    raw = json.loads((path or SEED_PATH).read_text(encoding="utf-8"))
    return [ThreatCommunitySeed.model_validate(r) for r in raw]


def seed_to_row_kwargs(seed: ThreatCommunitySeed) -> dict[str, Any]:
    """Seed -> column kwargs: JSON columns as plain dicts/lists (tcap may be None), reviewed_at as a date."""
    d = seed.model_dump(mode="json")
    d["reviewed_at"] = seed.reviewed_at
    return d
