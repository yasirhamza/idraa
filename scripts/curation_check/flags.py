"""Flags, per-check results and the ranked queue (spec §4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scripts.curation_check.config import BELOW_CUT_WINDOW, MAX_NAMED


@dataclass(frozen=True)
class Flag:
    check: str
    key: str  # stable across runs, e.g. "scenario-labels:<slug>:asset_class"
    subject: str  # queue-row label; starts with the slug so ties sort by slug
    score: float  # higher = stronger disagreement with the library; orders the queue only
    finding: str  # one-line explanation for the curator (Markdown)
    detail: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


@dataclass
class CheckResult:
    check: str
    flags: list[Flag] = field(default_factory=list)
    errored: list[tuple[str, str]] = field(default_factory=list)  # (item_key, message)
    items: int = 0
    input_tokens: list[int] = field(default_factory=list)
    models: set[str] = field(default_factory=set)
    subjects: list[str] = field(default_factory=list)  # every job's item_key submitted to this
    # check (both errored and successfully judged) — lets a view distinguish "judged, zero flags"
    # from "never judged" without re-deriving it from res.flags/res.errored alone (methodology M1)

    @property
    def error_share(self) -> float:
        return len(self.errored) / self.items if self.items else 0.0

    @property
    def suppressed(self) -> int:
        return sum(1 for f in self.flags if f.detail.get("suppressed"))


def rank(flags: list[Flag], top: int, window: float = BELOW_CUT_WINDOW) -> tuple[list[Flag], int]:
    """Top-N queue of unsuppressed flags, plus how many dropped candidates scored within
    `window` of the last kept one."""
    ordered = sorted(
        (f for f in flags if not f.detail.get("suppressed")), key=lambda f: (-f.score, f.subject)
    )
    queue = ordered[:top]
    if not queue:
        return [], 0
    cut = queue[-1].score - window
    return queue, sum(1 for f in ordered[top:] if f.score >= cut)


DISPLAY_DP = 2  # findings print scores to 2 decimals, so ties are judged at that precision


def tied_with(ranked: list[tuple[str, float]], p: float) -> list[str]:
    """Names in `ranked` (sorted by -score, name) whose score equals p at the displayed precision."""
    return [n for n, q in ranked if round(q, DISPLAY_DP) == round(p, DISPLAY_DP)]


def name_list(names: list[str], cap: int = MAX_NAMED) -> str:
    shown = ", ".join(f"**{n}**" for n in names[:cap])
    return shown + (f" +{len(names) - cap} more" if len(names) > cap else "")
