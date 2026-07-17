"""Pure text-humanization helpers shared across the app + service layers.

``humanize_slug`` was previously defined inline in ``idraa.app`` (a Jinja
filter). It is DISPLAY-ONLY (never mutates stored values) and pure, so it lives
here in a leaf module: ``idraa.app`` re-exports it for its filter registration
(and the existing ``from idraa.app import _humanize_slug`` test import), and
service-layer code (e.g. the verification workbook) can import it WITHOUT creating
an import cycle back through the FastAPI app.
"""

from __future__ import annotations

import re

# Revenue-tier numeric+unit token, e.g. "100m", "1b" -> "100M", "1B".
_TIER_TOKEN_RE = re.compile(r"^\d+[mb]$")
# Lowercase joiner words kept lowercase in humanized labels (e.g. "100m_to_1b").
_HUMANIZE_JOINERS = frozenset({"to", "of", "and", "or"})
# FAIR-CAM domain/sub-function acronym tokens uppercased in humanized labels
# rather than merely capitalized (e.g. "dsc_prev_sa_reporting" -> "DSC Prev
# SA Reporting", "vmc_id_control_monitoring" -> "VMC ID Control Monitoring").
_HUMANIZE_ACRONYMS = frozenset({"lec", "vmc", "dsc", "sa", "id", "tef", "roi"})


def humanize_slug(value: object) -> str:
    """Humanize an enum/slug string for DISPLAY ONLY (never for stored values).

    Underscores (and hyphens) become spaces, words are title-cased, and any
    existing acronym runs (words that already carry an uppercase letter) are
    preserved verbatim rather than lowercased. Two display-oriented
    special-cases:

    - Revenue-tier unit tokens: ``100m`` -> ``100M``, ``1b`` -> ``1B``.
    - Small joiner words (``to``/``of``/``and``/``or``) stay lowercase, so
      ``"100m_to_1b"`` -> ``"100M to 1B"`` and ``"loss_event"`` -> ``"Loss Event"``.
    - Known FAIR-CAM acronym tokens (``lec``/``vmc``/``dsc``/``sa``/``id``/
      ``tef``/``roi``) are uppercased rather than merely capitalized, so
      ``"dsc_prev_sa_reporting"`` -> ``"DSC Prev SA Reporting"``.

    Examples:
        decision_support    -> "Decision Support"
        variance_management -> "Variance Management"
        100m_to_1b          -> "100M to 1B"
        other               -> "Other"
        TPRM_control        -> "TPRM Control"
        dsc_prev_sa_reporting -> "DSC Prev SA Reporting"

    Returns "" for ``None``/empty input. Pure function (unit-tested); callers
    pass the raw slug in a ``title=`` tooltip to keep the value auditable.
    """
    if value is None:
        return ""
    text = str(value)
    if not text:
        return text
    words: list[str] = []
    for token in text.replace("-", "_").split("_"):
        if not token:
            continue
        lower = token.lower()
        if _TIER_TOKEN_RE.match(lower):
            words.append(lower.upper())
        elif lower in _HUMANIZE_JOINERS:
            words.append(lower)
        elif lower in _HUMANIZE_ACRONYMS:
            words.append(lower.upper())
        elif any(ch.isupper() for ch in token):
            # Preserve existing acronym runs (e.g. "TPRM", "iso27001Foo").
            words.append(token)
        else:
            words.append(token.capitalize())
    return " ".join(words)
