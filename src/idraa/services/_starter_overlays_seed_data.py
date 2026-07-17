"""V3-owned starter overlay seed data.

Relocated from ``fair_cam.parameters.overlays`` (PR pi F12). v3 owns the
overlay master-data CRUD and seeds its catalog from this module, so the
fair_cam dependency is purely a math layer (no v3-specific reference data).

This module is intentionally data-only: no math, no DB access, no fair_cam
imports. The seeding service in :mod:`idraa.services.overlays` consumes
``STARTER_OVERLAYS`` (light dataclass shape) + ``STARTER_OVERLAY_PROVENANCE``
(narrative payload keyed by tag) to construct ``OverlayDefinition`` rows.

Do not import fair_cam here — the original ``OverlayMultiplier`` dataclass
in ``fair_cam.parameters.overlays`` will be deleted in F11. The local
:class:`StarterOverlay` mirrors the four data fields the seeder needs.

Keep ``STARTER_OVERLAYS`` and ``STARTER_OVERLAY_PROVENANCE`` aligned: every
``tag`` in ``STARTER_OVERLAYS`` MUST have a matching entry in
``STARTER_OVERLAY_PROVENANCE`` with a non-empty ``methodology`` string. The
seeder in :mod:`idraa.services.overlays` raises ``RuntimeError`` if any
tag is missing methodology, defending against silent dangling references.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class StarterOverlay:
    """Plain seed-data dataclass for one starter overlay.

    Mirrors the four fields the seeder in
    :func:`idraa.services.overlays.seed_starter_overlays_for_org` reads:
    tag, display_name, frequency_multiplier, magnitude_multiplier. v3-owned
    replacement for the deleted ``fair_cam.parameters.overlays.OverlayMultiplier``.
    """

    tag: str
    display_name: str
    frequency_multiplier: float
    magnitude_multiplier: float


STARTER_OVERLAYS: Final[tuple[StarterOverlay, ...]] = (
    StarterOverlay(
        tag="critical_infrastructure",
        display_name="Critical Infrastructure (CISA-designated)",
        frequency_multiplier=1.4,
        magnitude_multiplier=2.0,
    ),
    StarterOverlay(
        tag="defense_industrial_base",
        display_name="Defense Industrial Base (DIB)",
        frequency_multiplier=1.8,
        magnitude_multiplier=2.5,
    ),
    StarterOverlay(
        tag="regulated_financial",
        display_name="Regulated Financial Sector",
        frequency_multiplier=1.2,
        magnitude_multiplier=1.8,
    ),
)


STARTER_OVERLAY_PROVENANCE: Final[dict[str, dict[str, object]]] = {
    "critical_infrastructure": {
        "sources": (
            "docs/reference/calibration-sources/ic3_2025.md#critical-infrastructure",
            "docs/reference/calibration-sources/cisa_year_in_review_2024.md",
        ),
        "methodology": (
            "TEF +40%: nation-state and criminal targeting both elevated for CI "
            "designations (IC3 2025 critical-infrastructure section ranks healthcare "
            "CI as top ransomware target; CISA YiR 2024 cites elevated advisory "
            "rates across all 16 sectors). Magnitude x2.0: when CI orgs are hit, "
            "downstream operational and regulatory loss components push median "
            "realized loss above IRIS sector median; anchored to IRIS Healthcare-"
            "as-CI subset analysis (Figure 12 trend panel)."
        ),
    },
    "defense_industrial_base": {
        "sources": (
            "docs/reference/calibration-sources/cisa_dib_advisories.md",
            "FAIR_PRIOR_ONLY",  # methodology covers the prior reasoning
        ),
        "methodology": (
            "Frequency +80% reflects nation-state targeting concentration on DIB "
            "participants (per CISA DIB advisories and public APT reporting). "
            "Magnitude x2.5 reflects extreme-tail IP-loss scenarios that dominate "
            "the loss curve for defense contractors (cleared-program data, weapons-"
            "system designs, supply-chain compromise). FAIR-prior anchored: no "
            "single empirical study quantifies DIB-specific multipliers; this is "
            "a documented expert estimate."
        ),
    },
    "regulated_financial": {
        "sources": (
            "docs/reference/calibration-sources/sec_cyber_disclosures.md",
            "docs/reference/calibration-sources/ffiec_advisories.md",
        ),
        "methodology": (
            "Frequency +20%: regulated-sector targeting elevated (SEC cyber-"
            "disclosure pattern + FFIEC advisory frequency). Magnitude x1.8: "
            "regulatory-cost overlay (mandatory disclosure, examination, fines, "
            "remediation reporting) on top of IRIS sector median. Anchored to "
            "FFIEC member-bank claims data extrapolated to regulated-sector "
            "loss-event magnitude."
        ),
    },
}
