"""Draft-exclusion totality tripwire (epic #34 P1a, spec §4).

Any new code that queries Scenario rows must be added to AUDITED with an
explicit draft-handling decision, or this test fails. Enumeration is a
source-pattern sweep over every KNOWN query idiom (select / db.get / join /
selectinload / aliased / repo construction) — a tripwire, not a proof.
Accepted blind spots (plan-gate Arch-I2/Spec-I5): raw SQL and relationship
loads reached from OTHER entities' queries; anyone adding those for Scenario
must extend QUERY_RE alongside.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "idraa"

# path -> decision ("excludes-drafts" | "shows-all-by-design" | "run-committed-upstream-gated")
AUDITED = {
    "routes/runs.py": "excludes-drafts",  # picker filters ACTIVE (P1a T2)
    "services/runs.py": "excludes-drafts",  # server-side gate (P1a T1)
    "services/run_executor.py": "run-committed-upstream-gated",  # defense-in-depth guard (P1a T1)
    "repositories/scenario_repo.py": "shows-all-by-design",  # primitives; callers decide
    "routes/scenarios.py": "shows-all-by-design",  # list/view/export show drafts (spec §4)
    "services/scenarios.py": "shows-all-by-design",  # CRUD on explicit ids
    "services/dashboard.py": "excludes-drafts",  # ACTIVE-only counts (P1a T2)
    "services/attack_coverage.py": "excludes-drafts",  # pre-existing ACTIVE filter
    "services/scenario_import.py": "shows-all-by-design",  # dedup vs ALL statuses (converter parity, spec §3.1)
    "services/reports.py": "run-committed-upstream-gated",
    "services/qualitative_converter.py": "shows-all-by-design",  # dedup reads ALL statuses incl DRAFT (spec §3.1)
}

QUERY_RE = re.compile(
    r"select\([^)]*\bScenario\b"  # select(Scenario…), select(func.x(Scenario.…
    r"|\.get\(\s*Scenario\b"  # db.get(Scenario, id)
    r"|\bjoin\(\s*Scenario\b"  # .join(Scenario, …)
    r"|selectinload\(\s*Scenario\."  # selectinload(Scenario.rel)
    r"|aliased\(\s*Scenario\b"  # aliased(Scenario)
    r"|ScenarioRepo\(",
    re.S,
)


def test_every_scenario_query_site_is_audited() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if QUERY_RE.search(path.read_text(encoding="utf-8")) and rel not in AUDITED:
            offenders.append(rel)
    assert not offenders, (
        f"unaudited Scenario query sites {offenders}: add each to AUDITED in "
        "tests/arch/test_draft_exclusion_sweep.py with an explicit draft-handling decision"
    )


def test_audited_files_still_query_scenarios() -> None:
    stale = [
        rel
        for rel in AUDITED
        if not (SRC / rel).exists() or not QUERY_RE.search((SRC / rel).read_text(encoding="utf-8"))
    ]
    assert not stale, f"stale AUDITED entries (file gone or no longer queries Scenario): {stale}"
