"""Per-fieldset sanity floors per MD-6. Port of R/clean_answers.R:30-52."""

from __future__ import annotations

from typing import Literal

from ._types import ClampEvent


def clean_quantile_pair(
    low: float,
    high: float,
    fieldset: Literal["tef", "vuln", "pl", "sl"],
) -> tuple[tuple[float, float], ClampEvent | None]:
    """Per-fieldset sanity floors per MD-6.

    DEPARTURE FROM R (vuln fieldset): when ``low > 0.95``, R's
    ``clean_answers.R:34`` applies ``pmax(high, low)`` AFTER the 0.95 cap,
    so an input like ``(low=0.98, high=0.99)`` would yield ``(0.98, 0.98)``
    in R — i.e. R allows final ``high > 0.95`` in this rebroadcast path.

    We deliberately clamp to ``[0.05, 0.95]`` ALWAYS (final ``high <= 0.95``
    and final ``low <= 0.95``). Methodology rationale: vuln is a probability
    bounded by ``[0, 1]`` and analyst-elicited probabilities above 0.95 are
    typically calibration errors, not real beliefs; bounding the support to
    ``[0.05, 0.95]`` honors the FAIR semantic invariant. A true
    methodology-faithful port preserving R's exact ``pmax``-after-cap
    behavior is tracked as a phase-2 carry-over if exact R parity is ever
    required.
    """
    before = (low, high)
    rules: list[str] = []
    new_low, new_high = low, high
    if fieldset == "tef":
        if low == 0:
            new_low = 0.1
            rules.append("tef_zero_low_floor")
        if high == 0:
            new_high = 1.0
            rules.append("tef_zero_high_floor")
    elif fieldset == "vuln":
        # Meth-4 PR1 fix: floor low first; cap high to min(0.95, ...) so the
        # 0.95 cap survives the high_eq_low rebroadcast. R semantics never
        # permit final high > 0.95.
        if low < 0.05:
            new_low = 0.05
            rules.append("vuln_low_floor_0.05")
        if high > 0.95:
            new_high = 0.95
            rules.append("vuln_high_cap_0.95")
        if new_high < new_low:
            # rebroadcast both ends to satisfy [low, 0.95] simultaneously
            new_high = min(0.95, max(new_low, new_high))
            new_low = min(new_low, new_high)
            rules.append("vuln_high_eq_low_rebroadcast")
    elif fieldset in ("pl", "sl"):
        floor = 1000.0
        if low < floor:
            new_low = floor
            rules.append(f"{fieldset}_min_loss_floor_1000")
        if high < floor:
            new_high = floor
            rules.append(f"{fieldset}_min_loss_floor_1000_high")
    else:
        raise ValueError(f"Unknown fieldset: {fieldset!r}")
    if (new_low, new_high) == before:
        return ((new_low, new_high), None)
    return (
        (new_low, new_high),
        ClampEvent(
            rule="+".join(rules),
            before=before,
            after=(new_low, new_high),
        ),
    )
