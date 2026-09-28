"""report.md rendering, disposition parsing and the hit-rate tally (spec §5)."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from scripts.curation_check.config import BELOW_CUT_WINDOW, MIN_DECIDED_FOR_TUNING, WILSON_Z
from scripts.curation_check.flags import CheckResult, Flag, rank

CHECK_TITLES = {
    "scenario-labels": "Scenario label audit",
    "control-functions": "Control function audit",
    "overlap": "Scenario overlap",
    "gaps": "Coverage gaps",
}
CHECK_ORDER = tuple(CHECK_TITLES)
SUB_TITLES = {"missing": "Possibly missing", "wrong": "Possibly wrong"}
DISPOSITIONS = ("accepted", "rejected", "deferred")
DISCLAIMER = (
    "Scores only order the queues. They are not calibrated probabilities: in the System One trial the judge's "
    "yes/no answers on control functions had a calibration error of 0.14, and 13 of 55 asset-class answers it "
    "gave at 0.99 or higher were wrong."
)
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


def md_cell(text: object) -> str:
    return " ".join(str(text).split()).replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|")


def split_top(top: int) -> tuple[int, int]:
    """Control audit: (possibly-missing, possibly-wrong) queue sizes."""
    return (top + 1) // 2, top // 2


def queues(res: CheckResult, top: int) -> list[tuple[str | None, list[Flag], int]]:
    if res.check != "control-functions":
        queue, below = rank(res.flags, top)
        return [(None, queue, below)]
    out: list[tuple[str | None, list[Flag], int]] = []
    for sub, size in zip(("missing", "wrong"), split_top(top), strict=True):
        queue, below = rank([f for f in res.flags if f.detail.get("direction") == sub], size)
        out.append((sub, queue, below))
    return out


def queue_keys(results: dict[str, CheckResult], top: int) -> dict[str, list[str]]:
    return {
        check: [f.key for _, q, _ in queues(res, top) for f in q] for check, res in results.items()
    }


def _table(queue: list[Flag]) -> list[str]:
    if not queue:
        return ["_No candidates._"]
    rows = ["| # | Score | Subject | Finding | Disposition | Reason |", "|---|---|---|---|---|---|"]
    return rows + [
        f"| {i} | {f.score:.2f} | {md_cell(f.subject)} | {md_cell(f.finding)} |  |  |"
        for i, f in enumerate(queue, 1)
    ]


def render(
    meta: dict[str, str],
    results: dict[str, CheckResult],
    top: int,
    previous: dict[str, list[str]] | None = None,
) -> str:
    lines = [
        f"# Curation check: {meta['campaign']} ({meta['date']})",
        "",
        f"Judge `{meta['judge']}` · model `{meta['model']}` · commit `{meta['git_commit'][:10]}` · top {top} per check",
        "",
        DISCLAIMER,
        "",
        "Mark each row's **Disposition** as `accepted`, `rejected` or `deferred`, with a one-line **Reason**. "
        "Flags are candidates for review, not verdicts.",
        "",
    ]
    for check in CHECK_ORDER:
        if check not in results:
            continue
        res = results[check]
        live = len(res.flags) - res.suppressed
        summary = f"{live} candidates from {res.items} item{'s' if res.items != 1 else ''}"
        if res.suppressed:
            summary += f"; {res.suppressed} suppressed as deliberately dropped claims (seed `_meta.claim_drops`)"
        lines += [f"## {CHECK_TITLES[check]}", "", summary + ".", ""]
        for sub, queue, below in queues(res, top):
            if sub:
                lines += [f"### {SUB_TITLES[sub]}", ""]
            lines += [
                f"Top {len(queue)}; {below} more scored within {BELOW_CUT_WINDOW:.2f} below the cut.",
                "",
            ]
            lines += _table(queue)
            lines.append("")
        suppressed = sorted(
            (f for f in res.flags if f.detail.get("suppressed")),
            key=lambda f: (-f.score, f.subject),
        )
        if suppressed:  # N-N3r2: dropped on grounding grounds, so a strong yes-score stays visible
            lines += [
                "<details><summary>Suppressed: deliberately dropped claims (seed <code>_meta.claim_drops</code>)</summary>",
                "",
            ]
            lines += [
                f"- {md_cell(f.subject)}: yes-score {f.score:.2f}; dropped because {md_cell(f.detail.get('reason', ''))}"
                for f in suppressed
            ]
            lines += ["", "</details>", ""]
        if res.errored:
            lines += [f"**Errored items ({len(res.errored)}):**", ""]
            lines += [f"- `{key}`: {md_cell(msg)}" for key, msg in res.errored]
            lines.append("")
        if check == "gaps":
            closest = sorted(
                (f for f in res.flags if f.detail.get("closest_p", 0.0) >= 0.8 and f.score < 0.1),
                key=lambda f: f.subject,
            )
            if closest:
                lines += ["**Judge's closest match (not reviewed):**", ""]
                lines += [
                    f"- {md_cell(f.subject)} → {md_cell(f.detail['closest'])} ({f.detail['closest_p']:.2f})"
                    for f in closest
                ]
                lines.append("")
    if previous is not None:
        now_keys = queue_keys(results, top)
        lines += ["## Before / after", ""]
        for check in CHECK_ORDER:
            if check not in results or check not in previous:
                continue
            pos = {k: i for i, k in enumerate(now_keys[check], 1)}
            gone = [k for k in previous[check] if k not in pos]
            still = [f"{k} (now #{pos[k]})" for k in previous[check] if k in pos]
            new = [k for k in now_keys[check] if k not in previous[check]]
            lines += [
                f"**{CHECK_TITLES[check]}:** {len(gone)} gone, {len(still)} still flagged, {len(new)} new.",
                "",
            ]
            lines += (
                [f"- gone: `{k}`" for k in gone]
                + [f"- still flagged: `{s}`" for s in still]
                + [f"- new: `{k}`" for k in new]
            )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_dispositions(text: str, source: str) -> dict[str, list[tuple[int, str, str]]]:
    """{check or check/sub: [(rank, subject, disposition or '')]} from a (curator-edited) report."""
    title_to_check = {v: k for k, v in CHECK_TITLES.items()}
    title_to_sub = {v: k for k, v in SUB_TITLES.items()}
    out: dict[str, list[tuple[int, str, str]]] = {}
    current: str | None = None
    sub: str | None = None
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            current, sub = title_to_check.get(line[3:].strip()), None
            continue
        if line.startswith("### "):
            sub = title_to_sub.get(line[4:].strip())
            continue
        if current is None or not line.startswith("|"):
            continue
        cells = [c.strip() for c in _CELL_SPLIT.split(line)[1:-1]]
        if len(cells) != 6 or not cells[0].isdigit():
            continue
        disposition = cells[4].lower()
        if disposition and disposition not in DISPOSITIONS:
            raise ValueError(
                f"{source}:{n}: unknown disposition {cells[4]!r} (use accepted, rejected or deferred)"
            )
        key = f"{current}/{sub}" if sub else current
        out.setdefault(key, []).append((int(cells[0]), cells[2], disposition))
    return out


def wilson(k: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def tally(paths: Iterable[Path]) -> dict[tuple[str, str], dict[str, Any]]:
    """Pool dispositions across all committed reports, grouped by (model, check). Each subject counts once
    across campaigns (N-I5r2): the latest decided disposition wins and a later blank never erases an earlier
    decision, so a rejected flag that re-queues every campaign is not counted again each time."""
    runs = []
    for p in paths:
        meta_path = p.parent / "run.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        runs.append(
            (
                meta.get("date", ""),
                str(p),
                meta.get("model", "unknown"),
                meta.get("campaign", p.parent.name),
                p,
            )
        )
    latest: dict[tuple[str, str, str], tuple[int, int, str]] = {}
    for _date, _name, model, _campaign, p in sorted(runs):
        for check, rows in parse_dispositions(p.read_text(encoding="utf-8"), str(p)).items():
            for rank_, subject, disposition in rows:
                key = (model, check, subject)
                if disposition or key not in latest:
                    latest[key] = (rank_, len(rows), disposition)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for (model, check, _subject), (rank_, n, d) in latest.items():
        c = out.setdefault(
            (model, check),
            {
                "accepted": 0,
                "rejected": 0,
                "deferred": 0,
                "blank": 0,
                "low_accepted": 0,
                "low_rejected": 0,
            },
        )
        c[d or "blank"] += 1
        if rank_ > n - max(1, n // 3) and d in ("accepted", "rejected"):
            c["low_" + d] += 1
    for c in out.values():
        decided = c["accepted"] + c["rejected"]
        low = c["low_accepted"] + c["low_rejected"]
        c["decided"] = decided
        c["hit_interval"] = wilson(c["accepted"], decided)
        c["low_interval"] = wilson(c["low_accepted"], low)
        if decided < MIN_DECIDED_FOR_TUNING:
            c["verdict"] = f"not enough decided rows ({decided} of {MIN_DECIDED_FOR_TUNING})"
        elif low and c["low_interval"][0] > 0.5:
            c["verdict"] = "raise --top"
        elif c["hit_interval"][1] < 0.2:
            c["verdict"] = "shrink --top or retire the check"
        else:
            c["verdict"] = "keep"
    return out
