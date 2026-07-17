"""Milestone B (#loss-pert-overhaul) shared triage helpers: the owner-approved
catastrophic shortlist + the mechanical lognormal->PERT conversion expectation."""

from __future__ import annotations

import math

Z = 1.6448536269514722

CATASTROPHIC_SLUGS = frozenset(
    {
        "chemical-process-safety-attack",
        "safety-system-bypass",
        "unauthorized-plc-modification",
        "field-instrument-spoofing",
        "grid-protective-relay-manipulation",
        "denial-of-control",
        "pipeline-scada-integrity",
        "nation-state-ics-supply-chain",
        "solarwinds-class-supply-chain",
        "telecom-lawful-intercept-nationstate-compromise",
        # Attack-coverage gap-fill epic (#529 Task 1): W1, owner-approved
        # 2026-07-09 (C2 -- nation-state, self-propagating wiper, unbounded
        # blast radius; NotPetya's Maersk loss dwarfs the transportation_
        # logistics sector p95).
        "destructive-wiper-nationstate",
    }
)


def expected_pert_from_lognormal(mean: float, sigma: float) -> tuple[float, float]:
    """(low, high) the Milestone B conversion produces; mode == low by rule."""
    return (
        round(math.exp(mean - Z * sigma), 10),
        round(math.exp(mean + Z * sigma), 10),
    )
