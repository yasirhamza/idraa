"""Form-parse + form-render helpers for :mod:`idraa.routes.scenarios`.

Extracted out of ``routes/scenarios.py`` in E7 to make headroom for the
two refresh-calibration handlers without busting the project-wide
700-line ceiling. These helpers have no dependency on the router itself
— they're pure (input-bound) coercion + (template-bound) rendering, so
moving them out is a no-op for behaviour.

Public surface:

- :data:`REVENUE_TIER_CHOICES` / :data:`INDUSTRY_CHOICES` — option lists
  for the create / edit form selects. Sourced from fair_cam keys (P5
  paranoid-review fix) so they can never drift from
  :class:`idraa.schemas.scenario.ScenarioForm`'s validators.
- :func:`flatten_validation_errors` — Pydantic error message extraction
  (mirror of the matching helper in :mod:`idraa.routes.overlays`).
- :func:`pert_dist_from_raw` / :func:`pert_dist_to_form` — round-trip
  PERT distribution between flat ``{prefix}_low/_mode/_high`` form
  fields and the JSON column shape.
- :func:`parse_expected_row_version` — collapses the int-cast for the
  hidden optimistic-lock form field to a single typed helper.
- :func:`parse_scenario_form` / :func:`form_from_scenario` /
  :func:`form_defaults` — the create / edit / defaults round-trippers.
- :func:`render_scenario_form` — error-branch template render.
- :func:`load_overlay_options` — DB-backed overlay-tag options for the
  form select.

PR pi F12 cleanup:
- ``mc_iterations`` and ``iris_calibration_year`` no longer flow through
  the scenario form. Mitigating-controls join still rides along.
- ``parse_scenario_form`` reads ``mitigating_control_ids`` (multi-value
  UUID list) and attaches them as ``form._mitigating_control_ids``.
- ``form_from_scenario`` / ``form_defaults`` drop the deceased fields;
  edit and create forms render without IRIS year / mc_iterations / overlay
  fieldsets.
- ``render_scenario_form`` accepts ``available_controls`` for the
  mitigating-controls multi-select fieldset.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from fair_cam.data.iris_2025 import (
    ANNUAL_INCIDENT_PROBABILITY_BY_REVENUE_TIER_2024 as _IRIS_TIERS_2025,
)
from fair_cam.quantile_pooling import lognormal_from_quantiles, lognormal_quantiles
from fastapi import Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from idraa.app import templates
from idraa.models.attack import DOMAIN_LABELS as _DOMAIN_LABELS  # #482 single-source
from idraa.models.attack import DOMAIN_ORDER as _DOMAIN_ORDER
from idraa.models.attack import AttackTactic, AttackTechnique
from idraa.models.control import Control
from idraa.models.enums import AssetClass, ScenarioEffect
from idraa.models.organization import Organization
from idraa.models.overlay import OverlayDefinition
from idraa.models.scenario import Scenario
from idraa.models.user import User
from idraa.schemas.scenario import ScenarioForm
from idraa.services.calibration import (
    CalibrationContext,
    calibration_context_from_org,
    org_industry_slug,
    revenue_tier_from_annual_revenue,
)
from idraa.services.industry_mapping import V3_TO_FAIR_CAM_INDUSTRY

__all__ = [
    "ASSET_CLASS_CHOICES",
    "ASSET_CLASS_LABELS",
    "ATTACK_VECTOR_CHOICES",
    "EFFECT_CHOICES",
    "EFFECT_LABELS",
    "INDUSTRY_CHOICES",
    "MAX_ATTACK_MAPPINGS",
    "REVENUE_TIER_CHOICES",
    "THREAT_ACTOR_TYPE_CHOICES",
    "THREAT_CATEGORY_CHOICES",
    "AttackFormContext",
    "asset_class_choices",
    "dist_from_raw",
    "dist_to_form",
    "extract_attack_mapping_ids",
    "flatten_validation_errors",
    "form_defaults",
    "form_from_scenario",
    "load_attack_form_context",
    "load_overlay_options",
    "org_industry_slug",
    "parse_expected_row_version",
    "parse_scenario_form",
    "pert_dist_from_raw",
    "pert_dist_to_form",
    "render_scenario_form",
    "revenue_tier_from_annual_revenue",
]

MAX_ATTACK_MAPPINGS = 200  # Sec-I2: bound submitted rows (catalog is ~280)

#  {1,6} is a designed rejection bound (MAX_ATTACK_MAPPINGS is 200, well
# under 6 digits), not an interpreter-limit inheritance from unbounded \d+.
_MAPPING_KEY_RE = re.compile(r"^attack_mappings\[(\d{1,6})\]\[technique_id\]$")


def extract_attack_mapping_ids(raw: dict[str, Any]) -> list[uuid.UUID]:
    """Pop attack_mappings[N][technique_id] keys; return deduped ids in row order.

    Runs BEFORE parse_scenario_form (ScenarioForm is extra="forbid" — leaving
    the keys in would 422 every submit; mirrors the entry_currency pre-pop at
    routes/scenarios.py:299). Raises ValueError (→ the route's existing 422
    path) on a non-UUID value or on more than MAX_ATTACK_MAPPINGS rows —
    urlencoded POSTs have no field-count guard, and an unbounded id list would
    blow the SQL variable limit downstream (Sec-I2).
    """
    indexed: list[tuple[int, str]] = []
    for key in list(raw):
        m = _MAPPING_KEY_RE.match(key)
        if m is None:
            continue
        value = raw.pop(key)
        if isinstance(value, str) and value.strip():
            indexed.append((int(m.group(1)), value.strip()))
    if len(indexed) > MAX_ATTACK_MAPPINGS:
        raise ValueError(f"too many technique mappings ({len(indexed)} > {MAX_ATTACK_MAPPINGS})")
    ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for _, value in sorted(indexed, key=lambda pair: pair[0]):
        tid = uuid.UUID(value)  # ValueError propagates → route's 422 handler
        if tid not in seen:
            seen.add(tid)
            ids.append(tid)
    return ids


@dataclass(frozen=True)
class AttackFormContext:
    """Render context for the scenario form's ATT&CK mapping rows."""

    groups_json: list[dict[str, Any]]  # rendered ONCE per page (data island)
    options: list[dict[str, Any]]  # flat deduped {"value","label"} per-row <select>
    rows: list[dict[str, Any]]  # {"index": int, "technique_uuid": str, "source": str}


async def load_attack_form_context(
    db: AsyncSession,
    *,
    scenario: Scenario | None = None,
    submitted_ids: list[uuid.UUID] | None = None,
) -> AttackFormContext:
    """Build combobox groups (domain → tactic, kill-chain order) + initial rows.

    ``submitted_ids`` wins over ``scenario`` — it re-renders the operator's
    in-flight selection on a 422 (mirrors form_raw precedence elsewhere).
    Deprecated techniques are excluded from the picker EXCEPT survivors: ids
    the scenario (or the in-flight submission) already maps render as flagged
    options so resubmits preserve them (Arch-I2).
    """
    rows: list[dict[str, Any]] = []
    if submitted_ids is not None:
        rows = [
            {"index": i, "technique_uuid": str(tid), "source": "user"}
            for i, tid in enumerate(submitted_ids)
        ]
    elif scenario is not None:
        rows = [
            {"index": i, "technique_uuid": str(m.technique_id), "source": m.source}
            for i, m in enumerate(scenario.attack_mappings)
        ]
    survivor_ids = {uuid.UUID(r["technique_uuid"]) for r in rows}

    tactics = (
        (
            await db.execute(
                select(AttackTactic).order_by(AttackTactic.domain, AttackTactic.display_order)
            )
        )
        .scalars()
        .all()
    )
    tech_filter: ColumnElement[bool] = AttackTechnique.deprecated.is_(False)
    if survivor_ids:
        tech_filter = or_(tech_filter, AttackTechnique.id.in_(survivor_ids))
    techniques = (
        (
            await db.execute(
                select(AttackTechnique).where(tech_filter).order_by(AttackTechnique.technique_id)
            )
        )
        .scalars()
        .all()
    )

    def _label(t: AttackTechnique) -> str:
        suffix = " (deprecated)" if t.deprecated else ""
        return f"{t.technique_id} — {t.name}{suffix}"

    by_domain_tactic: dict[tuple[str, str], list[AttackTechnique]] = {}
    for tech in techniques:
        for shortname in tech.tactics:
            by_domain_tactic.setdefault((tech.domain, shortname), []).append(tech)

    groups: list[dict[str, Any]] = []
    # Enterprise first, then group by domain, then kill-chain order within
    # domain — a future third domain (ATLAS) groups together instead of
    # interleaving by display_order across domains.
    ordered = sorted(
        tactics, key=lambda t: (_DOMAIN_ORDER.get(t.domain, 99), t.domain, t.display_order)
    )
    for tactic in ordered:
        group_options = [
            {
                "value": str(t.id),
                "label": _label(t),
                # Arch-I5: 80-char budget — the grouped blob is a per-page
                # data island, but it still ships once per page load.
                "description": (t.description or "")[:80],
            }
            for t in by_domain_tactic.get((tactic.domain, tactic.shortname), [])
        ]
        if group_options:
            groups.append(
                {
                    "domain": f"{tactic.domain}:{tactic.shortname}",
                    "label": f"{_DOMAIN_LABELS[tactic.domain]} — {tactic.name}",
                    "options": group_options,
                }
            )

    # Flat deduped option list for each row's hidden <select> (one option per
    # technique — NOT the per-tactic-duplicated grouped view).
    options = [{"value": str(t.id), "label": _label(t)} for t in techniques]

    return AttackFormContext(groups_json=groups, options=options, rows=rows)


# Sourced from fair_cam so this stays in lockstep with the IRIS
# calibration dict the schema validates against (P5 paranoid-review fix).
# The Pydantic schema E3 already validates against the same dict's keys,
# so a tier the form lets through is guaranteed to pass schema validation.
REVENUE_TIER_CHOICES: list[str] = list(_IRIS_TIERS_2025.keys())

# Calibratable industry subset — every key in V3_TO_FAIR_CAM_INDUSTRY has
# a fair_cam mapping, so the simulation runtime can compose against it.
# Sorted for stable form rendering.
INDUSTRY_CHOICES: list[str] = sorted(V3_TO_FAIR_CAM_INDUSTRY.keys())

# Human-readable labels for each AssetClass enum member.  Single source of
# truth — wizard, simple-form, and library-filter sidebar all derive from
# asset_class_choices() so they can never drift from the enum again.
# (Regression: enum gained CASH_OR_EQUIVALENT + 3 BUSINESS_PROCESS_* members
# on 2026-05-25 but hardcoded dropdowns were never updated, hiding those 4
# classes from authoring.)
ASSET_CLASS_LABELS: dict[AssetClass, str] = {
    AssetClass.DATA: "Data",
    AssetClass.SYSTEMS: "Systems",
    AssetClass.PEOPLE: "People",
    AssetClass.FACILITIES: "Facilities",
    AssetClass.OT_SYSTEMS: "OT systems",
    AssetClass.SAFETY_SYSTEMS: "Safety systems",
    AssetClass.CASH_OR_EQUIVALENT: "Cash or cash equivalent",
    AssetClass.BUSINESS_PROCESS_REVENUE: "Business process — revenue",
    AssetClass.BUSINESS_PROCESS_THIRD_PARTY_REVENUE: "Business process — third-party revenue",
    AssetClass.BUSINESS_PROCESS_COST: "Business process — cost",
    AssetClass.OTHER: "Other",
}


def asset_class_choices() -> list[tuple[str, str]]:
    """Return ``(value, label)`` pairs for all AssetClass members, in enum order.

    Callers that need a leading blank option (wizard, simple form) prepend
    ``[("", "— select —")]`` themselves; the filter sidebar does not.
    """
    return [(m.value, ASSET_CLASS_LABELS[m]) for m in AssetClass]


# Curated labels for the simple-form's enum dropdowns. Match the wizard's
# step_2_basic.html hardcoded labels so OT_* casing stays consistent across
# the two scenario-creation paths. (Dedupe follow-up: have the wizard
# template import these as well.)
THREAT_CATEGORY_CHOICES: list[tuple[str, str]] = [
    ("ransomware", "Ransomware"),
    ("malware", "Malware"),
    ("data_disclosure", "Data disclosure"),
    ("data_tampering", "Data tampering"),
    ("denial_of_service", "Denial of service"),
    ("social_engineering", "Social engineering"),
    ("physical_tampering", "Physical tampering"),
    ("supply_chain", "Supply chain"),
    ("insider_misuse", "Insider misuse"),
    ("ot_safety_tampering", "OT safety tampering"),
    ("ot_availability", "OT availability"),
    ("ot_integrity", "OT integrity (manipulation of view)"),
    ("miscellaneous", "Miscellaneous"),
]
THREAT_ACTOR_TYPE_CHOICES: list[tuple[str, str]] = [
    ("cybercriminals", "Cybercriminals"),
    ("nation_state", "Nation-state"),
    ("insider_malicious", "Insider — malicious"),
    ("insider_accidental", "Insider — accidental"),
    ("hacktivists", "Hacktivists"),
    ("competitors", "Competitors"),
]
ASSET_CLASS_CHOICES: list[tuple[str, str]] = asset_class_choices()
# Attack vectors — curated dropdown values. No AttackVector enum exists
# (column is free-text varchar(128)), so this list constrains UX without
# requiring a schema migration. Future entries can be added without
# breaking historical scenarios that stored other strings.
ATTACK_VECTOR_CHOICES: list[tuple[str, str]] = [
    ("email_phishing", "Email phishing"),
    ("drive_by_download", "Drive-by download"),
    ("supply_chain_compromise", "Supply-chain compromise"),
    ("exposed_service", "Exposed internet-facing service"),
    ("credential_compromise", "Credential compromise"),
    ("vulnerable_software", "Vulnerable software (unpatched CVE)"),
    ("removable_media", "Removable media"),
    ("physical_access", "Physical access"),
    ("insider_action", "Insider action"),
    ("cloud_misconfiguration", "Cloud misconfiguration"),
    ("third_party_access", "Third-party / vendor access"),
    ("other", "Other"),
]

# Human-readable labels for each ScenarioEffect (CIA triad). Single source of
# truth — form select and any future filter sidebars derive from EFFECT_CHOICES.
EFFECT_LABELS: dict[ScenarioEffect, str] = {
    ScenarioEffect.CONFIDENTIALITY: "Confidentiality",
    ScenarioEffect.INTEGRITY: "Integrity",
    ScenarioEffect.AVAILABILITY: "Availability",
}
EFFECT_CHOICES: list[tuple[str, str]] = [(m.value, EFFECT_LABELS[m]) for m in ScenarioEffect]


def flatten_validation_errors(exc: PydanticValidationError) -> list[str]:
    """Render ``err["msg"]`` only — never the raw Pydantic dict-repr.

    Mirrors :func:`idraa.routes.overlays._flatten_validation_errors`.
    """
    out: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "form"
        out.append(f"{loc}: {err['msg']}")
    return out


def pert_dist_from_raw(raw: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Build a PERT distribution dict from ``{prefix}_low/_mode/_high`` form keys.

    Bare ``KeyError`` / ``ValueError`` here propagate to the caller so
    :func:`parse_scenario_form` can render the form with a 422.
    """
    return {
        "distribution": "PERT",
        "low": float(raw[f"{prefix}_low"]),
        "mode": float(raw[f"{prefix}_mode"]),
        "high": float(raw[f"{prefix}_high"]),
    }


def dist_from_raw(raw: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Build a distribution dict for a NON-vuln node from form fields.

    Epic B (#326). Reads ``{prefix}_dist`` (default "pert"). For "lognormal",
    reads ``{prefix}_low``/``{prefix}_high`` as the p5/p95 pair and stores the
    native log-space ``{mean, sigma}`` via the closed form
    :func:`fair_cam.quantile_pooling.lognormal_from_quantiles`. For "pert"
    (or any unrecognised value), falls through to the existing PERT path.

    Vulnerability is a probability ∈ [0, 1] (FAIR Standard) and is therefore
    NOT routed through this helper — the caller keeps it on
    :func:`pert_dist_from_raw` so no ``vuln_dist`` field is ever honoured.

    ``lognormal_from_quantiles`` raises :class:`ValueError` for low<=0 / high<=0
    / high<low; that propagates so the route's existing
    ``except (..., ValueError)`` re-renders the form with a 422 — same contract
    as the bare ``KeyError`` / ``ValueError`` from :func:`pert_dist_from_raw`.
    """
    kind = (raw.get(f"{prefix}_dist") or "pert").strip().lower()
    if kind == "lognormal":
        low = float(raw[f"{prefix}_low"])
        high = float(raw[f"{prefix}_high"])
        return {"distribution": "lognormal", **lognormal_from_quantiles(low, high)}
    return pert_dist_from_raw(raw, prefix)


def _assert_probability_bounds(dist: dict[str, Any], field_name: str) -> None:
    """Issue #156: enforce [0, 1] on PERT triples that represent a probability.

    Vulnerability is the probability that a threat event becomes a loss
    event (FAIR Standard). All three points of its PERT triple must lie
    in ``[0.0, 1.0]``. Values outside this range have no probabilistic
    interpretation and would propagate into Monte Carlo sampling, yielding
    inflated ALE that no one can defend.

    Raises :class:`ScenarioFormValidationError` (a ``ValueError`` subclass)
    so the route's existing ``except (..., ValueError)`` catch re-renders
    the form with a 422.

    Other PERT distributions on a scenario form (TEF, primary loss, secondary
    loss) are NOT bounded above — TEF is a frequency and losses are dollar
    magnitudes. Caller is responsible for invoking this helper ONLY on fields
    that are probabilities.
    """
    for point_name in ("low", "mode", "high"):
        v = dist[point_name]
        if v < 0.0 or v > 1.0:
            raise ScenarioFormValidationError(
                f"{field_name}_{point_name}: probability must lie in [0.0, 1.0] "
                f"(got {v}). Vulnerability is the probability that a threat "
                f"event becomes a loss event (FAIR Standard); values outside "
                f"this range have no probabilistic interpretation."
            )


def pert_dist_to_form(dist: dict[str, Any], prefix: str) -> dict[str, str]:
    """Flatten a PERT distribution JSON back into form-string fields.

    Handles missing keys gracefully (returns empty string) so the partial
    is safe to call on a freshly created Scenario whose distributions might
    not have been validated yet.
    """
    return {
        f"{prefix}_low": str(dist.get("low", "")),
        f"{prefix}_mode": str(dist.get("mode", "")),
        f"{prefix}_high": str(dist.get("high", "")),
    }


def dist_to_form(dist: dict[str, Any] | None, prefix: str) -> dict[str, str]:
    """Round-trip a stored distribution back into flat form-string fields.

    Epic B (#326). Emits ``{prefix}_dist`` plus the fields the template needs:

    - lognormal: re-derive the p5/p95 quantiles from native ``{mean, sigma}``
      (so the operator edits the same real-space low/high they entered) and
      blank out ``{prefix}_mode`` (no mode in lognormal mode).
    - lognormal_mixture (#27): flatten to the TRUE mixture's p5/p95 (fair_cam
      bisection on an untruncated rebuild — the exact convention of
      ``scenario_export._dist_cells``) under the ``"lognormal"`` selector, and
      additionally emit ``{prefix}_from_mixture`` = component count. The form
      has no mixture editor, so saving REPLACES the pooled multi-expert
      mixture with a single lognormal anchored at the shown p5/p95 — that is
      lossy by design, and the ``_from_mixture`` flag drives a visible
      template warning so the replacement is informed, never silent (the
      silent-degradation class #27 exists to kill). On a 422 re-render the
      flag is absent (raw POST fields don't carry it) — correct, because by
      then the submitted values ARE plain-lognormal inputs.
    - PERT (or ``None`` / missing): delegate to :func:`pert_dist_to_form` and
      stamp ``{prefix}_dist = "pert"`` so the select renders the PERT option.

    Used for tef / pl / sl in :func:`form_from_scenario`. Vulnerability keeps
    :func:`pert_dist_to_form` directly (no selector; mixtures are barred from
    vulnerability at both the wizard and import gates).
    """
    kind = str((dist or {}).get("distribution", "")).lower()
    if dist and kind == "lognormal":
        lo, hi = lognormal_quantiles(dist["mean"], dist["sigma"], (0.05, 0.95))
        return {
            f"{prefix}_dist": "lognormal",
            f"{prefix}_low": str(lo),
            f"{prefix}_mode": "",
            f"{prefix}_high": str(hi),
        }
    if dist and kind == "lognormal_mixture":
        import math

        from fair_cam.quantile_pooling import (
            LogNormalTruncFit,
            LognormMixture,
            mixture_quantile_lognorm,
        )

        components = dist["components"]
        mixture = LognormMixture(
            components=tuple(
                LogNormalTruncFit(
                    meanlog=c["mean"],
                    sdlog=c["sigma"],
                    min_support=0.0,
                    max_support=math.inf,
                )
                for c in components
            ),
            weights=tuple(c["weight"] for c in components),
        )
        lo = mixture_quantile_lognorm(mixture, 0.05)
        hi = mixture_quantile_lognorm(mixture, 0.95)
        return {
            f"{prefix}_dist": "lognormal",
            f"{prefix}_low": str(lo),
            f"{prefix}_mode": "",
            f"{prefix}_high": str(hi),
            f"{prefix}_from_mixture": str(len(components)),
        }
    out = pert_dist_to_form(dist or {}, prefix)
    out[f"{prefix}_dist"] = "pert"
    return out


def parse_expected_row_version(raw: object) -> int | None:
    """Parse the hidden ``expected_row_version`` form field to int.

    Returns ``None`` on missing / non-int input — caller decides whether
    to render a 422 form or raise a 422 ``HTTPException`` (mirrors the
    existing per-route patterns; the helper just collapses the int-cast).
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class ScenarioFormValidationError(ValueError):
    """Raised by parse_scenario_form on out-of-range / malformed field values.

    Inherits from ValueError so the existing ``except (PydanticValidationError,
    KeyError, ValueError)`` catch in the route handlers picks it up without
    change.
    """


def parse_scenario_form(raw: dict[str, Any]) -> ScenarioForm:
    """Coerce raw form-data into a :class:`ScenarioForm` DTO.

    Numeric fields are pulled out and run through ``float()`` explicitly
    so the operator-facing error message is a sensible "not a number"
    rather than a Pydantic coercion path (B7 fold-in mirrors
    :mod:`idraa.routes.overlays`).

    Secondary loss is optional — if any of ``sl_low/mode/high`` is
    missing or empty, the entire ``secondary_loss`` distribution is
    set to None (matches the model's nullable column).

    ``mitigating_control_ids`` is a list of UUID strings extracted by
    the route via ``FormData.getlist``; each entry is coerced to
    ``uuid.UUID``. Invalid UUIDs raise :class:`ScenarioFormValidationError`.

    KeyError / ValueError / ValidationError bubble to the caller
    which 422-re-renders the form.

    PR pi: ``iris_calibration_year``, ``mc_iterations``, and
    ``overlay_tags`` are no longer parsed here. The first two live on
    the run-creation form; ``overlay_tags`` was vestigial after the
    calibration runtime was excised.
    """
    description = (raw.get("description") or "").strip() or None

    # Secondary loss is optional AND per-node distribution-typed (#326). For
    # lognormal it needs only low/high (p5/p95); for PERT it needs low/mode/high.
    # If the required fields for the selected kind are blank, the whole
    # distribution is None (matches the model's nullable column).
    sl_kind = (raw.get("sl_dist") or "pert").strip().lower()
    sl_low = str(raw.get("sl_low") or "").strip()
    sl_high = str(raw.get("sl_high") or "").strip()
    if sl_kind == "lognormal":
        if sl_low and sl_high:
            secondary_loss: dict[str, Any] | None = {
                "distribution": "lognormal",
                **lognormal_from_quantiles(float(sl_low), float(sl_high)),
            }
        else:
            secondary_loss = None
    else:
        sl_mode = str(raw.get("sl_mode") or "").strip()
        if sl_low and sl_mode and sl_high:
            secondary_loss = {
                "distribution": "PERT",
                "low": float(sl_low),
                "mode": float(sl_mode),
                "high": float(sl_high),
            }
        else:
            secondary_loss = None

    # mitigating_control_ids — multi-value field extracted by route via getlist.
    control_ids_raw = raw.get("mitigating_control_ids") or []
    if not isinstance(control_ids_raw, list):
        control_ids_raw = [control_ids_raw] if control_ids_raw else []
    parsed_control_ids: list[uuid.UUID] = []
    for cid_str in control_ids_raw:
        try:
            parsed_control_ids.append(uuid.UUID(str(cid_str)))
        except ValueError as exc:
            raise ScenarioFormValidationError(f"invalid control_id: {cid_str!r}") from exc

    # status / version / scenario_type / source ride along as hidden mirrors
    # on edit so the round-trip preserves non-default state; absent on create
    # → ScenarioForm's Pydantic defaults kick in (E6.a fix; str → StrEnum
    # coercion happens at validation time).
    passthrough: dict[str, Any] = {
        k: v
        for k in ("status", "version", "scenario_type", "source")
        if isinstance((v := raw.get(k)), str) and v
    }
    vulnerability_dist = pert_dist_from_raw(raw, "vuln")
    # Issue #156: vulnerability is a probability ∈ [0, 1] per FAIR Standard.
    # TEF (frequency) and PL/SL (dollar magnitudes) are NOT bounded above.
    _assert_probability_bounds(vulnerability_dist, "vuln")

    form = ScenarioForm(
        name=raw["name"],
        description=description,
        threat_category=raw["threat_category"],
        threat_actor_type=(raw.get("threat_actor_type") or "").strip() or None,
        attack_vector=(raw.get("attack_vector") or "").strip() or None,
        asset_class=(raw.get("asset_class") or "").strip() or None,
        effect=(raw.get("effect") or "").strip() or None,
        threat_event_frequency=dist_from_raw(raw, "tef"),
        vulnerability=vulnerability_dist,
        primary_loss=dist_from_raw(raw, "pl"),
        secondary_loss=secondary_loss,
        # industry/revenue_tier are no longer on ScenarioForm (issue #88 Task 9).
        # The service derives them from the live org row.
        **passthrough,
    )
    # Attach parsed extras as attributes so route handlers can thread them
    # into the Scenario row / ScenarioRepo.set_mitigating_controls without
    # re-parsing. ScenarioForm is a Pydantic model so extra fields are
    # rejected at construction; we attach to the returned object post-init.
    object.__setattr__(form, "_mitigating_control_ids", parsed_control_ids)
    return form


def form_from_scenario(s: Scenario) -> dict[str, Any]:
    """Flatten a Scenario row into the flat string-shape the form template
    expects (mirrors :func:`form_defaults`).

    The form template renders ``form.tef_low`` / ``form.industry`` etc. as
    plain string-typed values; the JSON-typed distribution columns are
    expanded into their PERT components so the form can re-render with
    pre-populated inputs on edit GET / 422 re-render.

    ``mitigating_control_ids`` is included so the edit form pre-populates
    that fieldset.
    """
    sl = s.secondary_loss or {}
    out: dict[str, Any] = {
        "name": s.name,
        "description": s.description or "",
        "threat_category": s.threat_category,
        "threat_actor_type": s.threat_actor_type or "",
        "attack_vector": s.attack_vector or "",
        "asset_class": s.asset_class or "",
        "effect": getattr(s.effect, "value", s.effect) or "",
        "mitigating_control_ids": [str(c.id) for c in (s.mitigating_controls or [])],
    }
    # tef / pl / sl are per-node distribution-typed (#326) — dist_to_form emits
    # the {prefix}_dist selector value + re-derived p5/p95 for lognormal nodes.
    # Vulnerability is PERT-only (probability ∈ [0, 1]); it keeps pert_dist_to_form.
    out.update(dist_to_form(s.threat_event_frequency, "tef"))
    out.update(pert_dist_to_form(s.vulnerability, "vuln"))
    out.update(dist_to_form(s.primary_loss, "pl"))
    if sl:
        out.update(dist_to_form(sl, "sl"))
    else:
        out.update({"sl_dist": "pert", "sl_low": "", "sl_mode": "", "sl_high": ""})
    return out


def form_defaults() -> dict[str, Any]:
    """Empty-form defaults for new-scenario render.

    Numeric distribution fields are blank so the operator types real
    values rather than tweaking placeholder digits.

    The ``name`` field is pre-filled with a timestamp-based default
    ("Scenario YYYY-MM-DD HH:MM") so the operator never has to type a
    name to satisfy the required-field validation. They can still
    override it with anything they like.
    """
    from datetime import datetime

    return {
        "name": f"Scenario {datetime.now():%Y-%m-%d %H:%M}",
        "description": "",
        "threat_category": "",
        "threat_actor_type": "",
        "attack_vector": "",
        "asset_class": "",
        "effect": "",
        # Per-node distribution selectors (#326) default to PERT. Vulnerability
        # has no selector (probability ∈ [0, 1], PERT-only) so no vuln_dist key.
        "tef_dist": "pert",
        "tef_low": "",
        "tef_mode": "",
        "tef_high": "",
        "vuln_low": "",
        "vuln_mode": "",
        "vuln_high": "",
        "pl_dist": "pert",
        "pl_low": "",
        "pl_mode": "",
        "pl_high": "",
        "sl_dist": "pert",
        "sl_low": "",
        "sl_mode": "",
        "sl_high": "",
        "mitigating_control_ids": [],
    }


def render_scenario_form(
    request: Request,
    *,
    user: User,
    org: Organization | None,
    scenario: Scenario | None,
    form_raw: dict[str, Any],
    overlay_options: list[dict[str, Any]],
    available_controls: list[Control] | None = None,
    inactive_linked_controls: list[Control] | None = None,
    attack_ctx: AttackFormContext | None = None,
    errors: list[str],
    status_code: int = 422,
) -> HTMLResponse:
    """Render the scenario form on an error branch of create / update.

    Deduplicates the 422 (parse / validation) and 409 (optimistic-lock)
    re-render blocks across :func:`idraa.routes.scenarios.create_scenario`
    and :func:`idraa.routes.scenarios.update_scenario`. ``form_raw`` is
    echoed back so the analyst doesn't lose in-progress input.

    ``available_controls`` is threaded into the template context for the
    mitigating-controls multi-select fieldset (F12).

    ``inactive_linked_controls`` (issue #217) is the set of controls that are
    LINKED to the scenario but no longer ACTIVE (so the ACTIVE-only
    ``available_controls`` list does not include them). The template renders
    them as checked + disabled so the operator can see the link still exists
    and is preserved server-side. Only meaningful on the edit path; create /
    error re-renders without a loaded scenario pass an empty list.

    ``org`` is used to derive ``org_industry`` / ``org_revenue_tier`` so the
    form chips render correctly on 422 / 409 re-renders (issue #88 Gap 1).

    ``attack_ctx`` (issue #475 T9) feeds the ATT&CK mapping fieldset's THREE
    strict-undefined-required template keys (groups island / per-row option
    list / initial rows). Callers on the create/update error paths pass an
    ``AttackFormContext`` built from the submitted ``technique_ids`` (or, on
    an extraction failure, from the persisted scenario) so a 422/409
    re-render doesn't silently drop the operator's in-flight technique tags.
    ``None`` degrades to empty lists — never omit the keys under
    strict-undefined Jinja.
    """
    ctx: CalibrationContext | None = calibration_context_from_org(org) if org is not None else None
    form_action = f"/scenarios/{scenario.id}" if scenario is not None else "/scenarios"
    return templates.TemplateResponse(
        request,
        "scenarios/form.html",
        {
            "current_user": user,
            "flash": None,
            "scenario": scenario,
            "form": form_raw,
            "overlay_options": overlay_options,
            "available_controls": available_controls or [],
            "inactive_linked_controls": inactive_linked_controls or [],
            "org_industry": ctx.industry if ctx is not None else None,
            "org_revenue_tier": ctx.revenue_tier if ctx is not None else None,
            "threat_category_choices": THREAT_CATEGORY_CHOICES,
            "threat_actor_type_choices": THREAT_ACTOR_TYPE_CHOICES,
            "asset_class_choices": ASSET_CLASS_CHOICES,
            "attack_vector_choices": ATTACK_VECTOR_CHOICES,
            "effect_choices": EFFECT_CHOICES,
            "attack_technique_groups_json": attack_ctx.groups_json if attack_ctx else [],
            "attack_technique_options": attack_ctx.options if attack_ctx else [],
            "attack_mapping_rows": attack_ctx.rows if attack_ctx else [],
            "form_action": form_action,
            "form_method": "post",
            "errors": errors,
        },
        status_code=status_code,
    )


async def load_overlay_options(db: AsyncSession, organization_id: Any) -> list[dict[str, Any]]:
    """Return ``[{"tag": ..., "current_version": ...}, ...]`` for the org.

    Sorted by tag for stable form rendering. Field ``current_version`` in
    the rendered label is the live ``OverlayDefinition.version`` at
    form-render time — the create handler re-resolves the pin at the
    moment of write to avoid TOCTOU drift.
    """
    rows = (
        await db.execute(
            select(OverlayDefinition.tag, OverlayDefinition.version)
            .where(OverlayDefinition.organization_id == organization_id)
            .where(OverlayDefinition.is_active.is_(True))
            .order_by(OverlayDefinition.tag)
        )
    ).all()
    return [{"tag": tag, "current_version": ver} for tag, ver in rows]
