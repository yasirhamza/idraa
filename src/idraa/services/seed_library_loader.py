"""Pydantic validator for seed_library_entries.json.

Used at migration time to validate every entry before DB insert, so any
seed-data corruption surfaces at `alembic upgrade` rather than silently at
first browse query.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Allowed revenue tier slugs — must match _REVENUE_TIER_SLUGS at
# services/library_calibration.py and the v3 tier taxonomy. Duplicated here
# instead of imported to keep the seed loader independent of the
# library_calibration runtime (Pydantic validator usable in isolation, e.g.
# CI-side schema checks).
_REVENUE_TIER_SLUGS: frozenset[str] = frozenset(
    {
        "less_than_10m",
        "10m_to_100m",
        "100m_to_1b",
        "1b_to_10b",
        "10b_to_100b",
        "more_than_100b",
    }
)


class LossFormEntry(BaseModel):
    """One active FAIR form of loss in an entry's loss_form_profile (D-i #497).

    See docs/reference/loss-magnitude-forms.md. This is provenance only -- the
    engine never reads it; primary_loss/secondary_loss remain the stored nodes.

    Bounds (security finding Sec1): loss_form_profile is stored GLOBALLY and
    re-served to every org from uploaded bundles. The bounds guard in
    library_bundle_import.py only caps TOP-LEVEL strings/lists, not strings
    nested inside these dicts -- so the caps MUST live here (validated
    pre-INSERT). extra="forbid" (Sec2) rejects typo'd/unknown form keys instead
    of silently dropping them.
    """

    model_config = ConfigDict(extra="forbid")

    form: Literal[
        "productivity",
        "response",
        "replacement",
        "fines",
        "competitive_advantage",
        "reputation",
    ]
    kind: Literal["primary", "secondary"]
    magnitude_basis: str = Field(min_length=1, max_length=512)
    citations: list[str] = Field(default=[], max_length=32)
    verified: bool = False
    composition_role: Literal["dominant", "contributing", "provenance_only"]
    # Epic D-iii (#497 Amendment A1): the form's SHARE of the cited sector envelope
    # (fraction in (0,1]). None for a beyond-envelope form (BEC/IP) whose magnitude
    # is its own distribution, not a share of the IRIS envelope. Shares are
    # analyst-judged (vulnerability-grade) — no per-form citation; the envelope is
    # the cited anchor. The joint Σ(shares) ≤ 1 bound is checked at the seed guard.
    share: float | None = None

    @field_validator("share")
    @classmethod
    def _share_in_unit_interval(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 < v <= 1.0):
            raise ValueError(f"share must be in (0, 1]; got {v!r}")
        return v

    @field_validator("citations")
    @classmethod
    def _cap_citation_len(cls, v: list[str]) -> list[str]:
        for c in v:
            if len(c) > 512:
                raise ValueError("citation exceeds 512 chars")
        return v


class LibraryEntrySeed(BaseModel):
    slug: str
    name: str
    status: str = Field(pattern="^(draft|published|deprecated)$")
    threat_event_type: str
    threat_actor_type: str
    asset_class: str
    attack_vector: str | None = None
    tags: list[str] = []
    description: str = Field(min_length=20)
    example_incidents: str | None = None
    source_citations: list[str] = []
    canonical_fair_gap: str = Field(min_length=20)
    applicable_industries: list[str] | None = None
    applicable_sub_sectors: list[str] | None = None
    applicable_org_sizes: list[str] | None = None
    threat_event_frequency: dict[str, Any]
    # methodology/vuln-inherent-framing: as of the inherent-anchor decision the
    # Scenario `vulnerability` is the asset's INHERENT (control-naive)
    # susceptibility. The seeded values (~44: 31 base + 13 extension) predate
    # that decision and were curated at typical/controlled posture, so under the
    # inherent frame they are anchored LOW. Re-curation upward (with provenance) is
    # deferred to the Epic C library re-curation (#335), tracked at #338. See
    # docs/reference/fair-cam-methodology.md → "Vulnerability anchor".
    vulnerability: dict[str, Any]
    primary_loss: dict[str, Any]
    secondary_loss: dict[str, Any] | None = None
    suggested_control_ids: list[str] = []
    standards_references: dict[str, Any] | None = None
    # PR gamma-4 (#115): calibration_anchor is REQUIRED. PR gamma-2 added the
    # field nullable; PR gamma-3 curated all 31 seed entries; this PR flips
    # the constraint so seed validation rejects entries without an anchor
    # at migration time.
    # C-iii-a: annotation widened to dict[str, str | None] because the two new
    # optional provenance keys (loss_anchor, vuln_posture) may carry None.
    calibration_anchor: dict[str, str | None]
    # Epic C-i (#335 §6): epistemic tier of the loss-magnitude anchor. Defaults
    # to 'anecdotal' so back-compat seed entries (no loss_tier key) parse and
    # land as PERT. Mirrors the LossTier enum + the scenario_library column.
    loss_tier: str = Field(default="anecdotal", pattern="^(paginated|vendor|anecdotal|none)$")
    # Milestone B (#loss-pert-overhaul): distribution-shape class. Defaults to
    # 'capped' so back-compat seed JSON (no key) parses; the conversion builder
    # stamps it explicitly on all 93 entries. Independent of loss_tier.
    loss_shape: str = Field(default="capped", pattern="^(capped|catastrophic)$")
    # Epic D-i (#497 §6): per-form provenance; defaults [] so back-compat seed
    # entries (no key) parse. D-iii populates it; the seed guard (§7) enforces
    # that a lognormal loss node has a matching verified-cited profile. The
    # outer max_length=12 caps the profile at the 6 forms x 2 sides ceiling (Sec1).
    loss_form_profile: list[LossFormEntry] = Field(default=[], max_length=12)

    @field_validator("canonical_fair_gap", "description")
    @classmethod
    def _not_whitespace_only(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must be non-whitespace")
        return v

    @field_validator("calibration_anchor")
    @classmethod
    def _validate_calibration_anchor(cls, v: dict[str, str | None]) -> dict[str, str | None]:
        """Shape: required keys {'industry', 'revenue_tier'} + optional {'loss_anchor', 'vuln_posture'}.

        Required keys:
          - 'industry': any non-empty string (advisory; IRIS Table 1 is industry-aggregate)
          - 'revenue_tier': one of the six IRIS revenue tier slugs

        Allowed optional keys (C-iii-a provenance fields):
          - 'loss_anchor': free-text note describing the loss calibration source
          - 'vuln_posture': free-text note describing the vulnerability posture frame

        Any other key is rejected as a likely typo (keeps the validator useful as
        a data-integrity gate at migration time).
        """
        if not isinstance(v, dict):
            raise ValueError("calibration_anchor must be a dict")
        required = {"industry", "revenue_tier"}
        allowed_extras: frozenset[str] = frozenset({"loss_anchor", "vuln_posture"})
        unknown = set(v.keys()) - required - allowed_extras
        if unknown:
            raise ValueError(
                f"calibration_anchor has unknown key(s) {sorted(unknown)!r}; "
                f"only {sorted(required | allowed_extras)!r} are permitted"
            )
        if not required.issubset(v.keys()):
            missing = required - set(v.keys())
            raise ValueError(f"calibration_anchor is missing required key(s) {sorted(missing)!r}")
        industry = v["industry"]
        revenue_tier = v["revenue_tier"]
        if not isinstance(industry, str) or not industry:
            raise ValueError("calibration_anchor.industry must be a non-empty string")
        if revenue_tier not in _REVENUE_TIER_SLUGS:
            raise ValueError(
                f"calibration_anchor.revenue_tier must be one of "
                f"{sorted(_REVENUE_TIER_SLUGS)} (got: {revenue_tier!r})"
            )
        return v
