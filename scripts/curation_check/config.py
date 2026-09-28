"""Tunable constants for the curation checker (spec sections 4-6)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

JEV_MODEL = "jev-1.13.0"  # pinned; never the moving `jev-latest` alias
JEV_BASE_URL = "https://api.typesafe.ai"  # pinned; TYPESAFE_BASE_URL is deliberately ignored
JEV_TIMEOUT_S = 60.0

DEFAULT_TOP = 15
BELOW_CUT_WINDOW = 0.10
TOKEN_WARN_AT = 24_000  # 75% of Jev 1.13's 32k state + longest-question limit
MAX_ERROR_SHARE = 0.05
INTAKE_TITLE_MAX = 80  # intake titles are published in committed reports

MIN_DECIDED_FOR_TUNING = 10
WILSON_Z = 1.645  # two-sided 90% interval

GAP_COVERED_NONE_MAX = 0.10
GAP_COVERED_CLOSEST_MIN = 0.80  # gaps: below/above these, show the judge's unreviewed closest match
