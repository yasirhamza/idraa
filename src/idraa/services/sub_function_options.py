"""Build the option-data structure consumed by the sub-function combobox.

The Tier-2 picker (Alpine.js combobox at static/js/sub_function_combobox.js)
needs each sub-function flattened into {value, label, description, domain,
standard_ref} so it can render grouped + filtered options without
re-implementing the FAIR-CAM taxonomy on the client.

Pure read-only derivation from the enums + the description map; no I/O.
Cached via lru_cache since the inputs are module-level constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from idraa.models.enums import (
    SUB_FUNCTION_DESCRIPTIONS,
    ControlDomain,
    FairCamSubFunction,
    subfunction_to_domain,
)

_DOMAIN_LABELS: dict[ControlDomain, str] = {
    ControlDomain.LOSS_EVENT: "LEC — Loss Event Control",
    ControlDomain.VARIANCE_MANAGEMENT: "VMC — Variance Management Control",
    ControlDomain.DECISION_SUPPORT: "DSC — Decision Support Control",
}

# Domain render order: LEC first (most analyst-familiar), then VMC, then DSC.
_DOMAIN_ORDER: tuple[ControlDomain, ...] = (
    ControlDomain.LOSS_EVENT,
    ControlDomain.VARIANCE_MANAGEMENT,
    ControlDomain.DECISION_SUPPORT,
)

# Sub-functions the combobox should NOT offer: same set the existing
# template excludes from the dropdown (DSC_CORR_MISALIGNED is virtual per
# FAIR-CAM §5.3 — requires derived_from_assignment_id which the wizard
# doesn't expose yet).
_HIDDEN: frozenset[FairCamSubFunction] = frozenset({FairCamSubFunction.DSC_CORR_MISALIGNED})


@dataclass(frozen=True)
class SubFunctionOption:
    """One row in the combobox listbox."""

    value: str  # the FairCamSubFunction slug (form field value)
    label: str  # short human label (e.g. "Monitoring")
    description: str  # one-line FAIR-CAM definition
    domain: str  # domain key (loss_event / variance_management / decision_support)


@dataclass(frozen=True)
class SubFunctionGroup:
    """A domain bucket containing its sub-function options."""

    domain: str  # ControlDomain.value
    label: str  # display label (e.g. "LEC — Loss Event Control")
    options: tuple[SubFunctionOption, ...]


def _short_label(description: str) -> str:
    """Extract the leading short label from a description string.

    Convention used in SUB_FUNCTION_DESCRIPTIONS: each entry is shaped
    as ``"<short label> — <longer explanation>"`` (em dash separator).
    Falls back to the full string if the separator is absent.
    """
    head, _sep, _rest = description.partition(" — ")
    return head.strip() or description.strip()


@lru_cache(maxsize=1)
def build_sub_function_groups() -> tuple[SubFunctionGroup, ...]:
    """Return grouped + ordered sub-function options for the combobox.

    Cached: the inputs (SUB_FUNCTION_DESCRIPTIONS, subfunction_to_domain)
    are module-level constants so the result is stable across the
    process's lifetime.
    """
    buckets: dict[ControlDomain, list[SubFunctionOption]] = {d: [] for d in _DOMAIN_ORDER}
    for sf in FairCamSubFunction:
        if sf in _HIDDEN:
            continue
        description = SUB_FUNCTION_DESCRIPTIONS.get(sf, sf.value)
        domain = subfunction_to_domain(sf)
        buckets[domain].append(
            SubFunctionOption(
                value=sf.value,
                label=_short_label(description),
                description=description,
                domain=domain.value,
            )
        )

    return tuple(
        SubFunctionGroup(
            domain=domain.value,
            label=_DOMAIN_LABELS[domain],
            options=tuple(buckets[domain]),
        )
        for domain in _DOMAIN_ORDER
        if buckets[domain]
    )


def groups_to_json_safe() -> list[dict]:  # type: ignore[type-arg]
    """Convert groups to a JSON-serialisable shape (for the template global).

    The Alpine component reads this as a JS array; keeping it pure JSON
    means we can drop it straight into a `<script>` block without
    custom serialisation hacks.
    """
    return [
        {
            "domain": g.domain,
            "label": g.label,
            "options": [
                {
                    "value": o.value,
                    "label": o.label,
                    "description": o.description,
                    "domain": o.domain,
                }
                for o in g.options
            ],
        }
        for g in build_sub_function_groups()
    ]
