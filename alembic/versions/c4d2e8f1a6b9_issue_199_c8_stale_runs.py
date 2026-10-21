"""#199 slice 1: flag stale every COMPLETED run whose result the C8 engine change moves.

Revision ID: c4d2e8f1a6b9
Revises: b7c1a9e3d2f4
Create Date: 2026-10-15

The VMC Correction group became implementation-anchored (register C8): a run whose
control set carried ``vmc_corr_treatment_selection`` beside an Implementation carrier
AND an Identification member can only score higher (E_corr(after) = i >= i*t =
E_corr(before); the pair is AND(E_id, E_corr), so without an Identification member
E_meta does not move either). The criterion here is DELIBERATELY A SUPERSET of those
runs: membership is tested on the sub-function alone — the deterministic,
judgment-free criterion (spec §7.2) — so a run whose snapshot carries Treatment
Selection without Implementation, or without any vmc_id_* member, is flagged although
its stored E_meta, r_eff and every dollar figure are unchanged (pinned by the
``r_ts_only`` fixture). Staleness is advisory (``control_library.py`` docstring): the
run stays COMPLETED and visible; the UI prompts a re-run; a re-run of an over-included
run reproduces its numbers. Tightening to co-presence was rejected because the V1
fallback below cannot see co-presence. Known under-count: a V1 snapshot (no
assignment detail) whose selection row was deleted after the run cannot be recovered
by the fallback.

Membership (R-DEP-4, plan-gate M-I4): decided from the run's OWN inputs. A run is
affected when its ``controls_snapshot`` (V2/V3 shape: ``[{"control_id", "assignments":
[{"sub_function", ...}]}]``, written at run_executor.py:1943) shows any control carrying
``vmc_corr_treatment_selection`` — so a selection claim removed from the control AFTER
the run (a form save deletes missing sub-functions, services/controls.py:208, and never
flags runs stale) still flags it. For a run whose snapshot carries no assignment detail
(V1 shape, empty, malformed) the fallback is ``control_ids_used`` ∩ the CURRENT carriers,
keyed on the run's own ``organization_id`` (``(org_hex, control_hex)`` pairs — the
``flag_runs_stale_for_control`` org-scoping, Sec-N1).

Selection: ``(id, organization_id, control_ids_used, controls_snapshot)`` of ``status =
'completed' AND is_stale = :f`` rows only — ``simulation_results`` is never selected (the
``flag_runs_stale_for_control`` column-restriction pattern); booleans are bound
parameters (Postgres-safe, A-N3). The candidate rows are materialised with one
``fetchall()`` (A2-N1, accepted): snapshots are per-control assignment lists (no sample
arrays), the migration runs before uvicorn starts on the 4 GB VM (fly.toml), and the
alternative — paging by rowid — buys nothing at the current run-table size; revisit
if ``controls_snapshot`` ever carries bulk data. Membership is Python-side because SQLite has no
JSON_CONTAINS; ``control_ids_used`` stores HYPHENATED ``str(UUID)`` while
``control_function_assignments.control_id`` is 32-hex, so every candidate id is
normalised through ``uuid.UUID`` first.

Audit: one ``audit_log`` row per flagged run, ``user_id = NULL`` (system actor),
``action = 'run.flagged_stale'``, ``entity_type = 'risk_analysis_run'``, ids bound as
the NO-HYPHEN hex read from the row (e3a1c4f7b2d9 precedent),
``changes = {"is_stale": [false, true], "reason": ["", "issue-199 C8 implementation-anchored correction"]}``.

Idempotent: already-stale runs are neither rewritten nor counted. Logs one counter
line (``controls`` = distinct current carriers, informational). No ``import math``,
no ``assert``. Downgrade: documented no-op (the audit rows preserve the transition).
``run_is_affected`` is pure and is reused by scripts/sweep_issue_199_prod_rows.py
(loaded by path) so the pre-deploy sweep and this migration never disagree.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "c4d2e8f1a6b9"
down_revision: str | Sequence[str] | None = "b7c1a9e3d2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_SUB_FUNCTION = "vmc_corr_treatment_selection"
_ACTION = "run.flagged_stale"
_REASON = "issue-199 C8 implementation-anchored correction"


def _hex(raw: object) -> str | None:
    try:
        return uuid.UUID(str(raw)).hex
    except (ValueError, TypeError, AttributeError):
        return None


def _json(raw: object) -> Any:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return None


def _snapshot_has_selection(raw_snapshot: object) -> bool | None:
    """True/False when the snapshot carries assignment detail; None when it does not
    (V1 shape, empty list, NULL or malformed) and the caller must fall back."""
    snap = _json(raw_snapshot)
    if not isinstance(snap, list):
        return None
    detail = False
    for control in snap:
        if not isinstance(control, dict):
            continue
        assignments = control.get("assignments")
        if not isinstance(assignments, list) or not assignments:
            continue
        detail = True
        for a in assignments:
            if isinstance(a, dict) and a.get("sub_function") == _SUB_FUNCTION:
                return True
    return False if detail else None


def run_is_affected(
    raw_snapshot: object,
    raw_ids: object,
    org_hex: str | None,
    carriers: set[tuple[str, str]],
) -> bool:
    """R-DEP-4 membership: the run's own snapshot decides when it carries assignment detail;
    otherwise control_ids_used ∩ current carriers, keyed on the run's own org."""
    by_snapshot = _snapshot_has_selection(raw_snapshot)
    if by_snapshot is not None:
        return by_snapshot
    if org_hex is None:
        return False
    ids = _json(raw_ids)
    if not isinstance(ids, list):
        return False
    used = {h for h in (_hex(x) for x in ids) if h is not None}
    return any((org_hex, h) in carriers for h in used)


def upgrade() -> None:
    bind = op.get_bind()
    carriers: set[tuple[str, str]] = set()
    for cid, org_id in bind.execute(
        sa.text(
            "SELECT DISTINCT control_id, organization_id FROM control_function_assignments "
            "WHERE sub_function = :sf"
        ),
        {"sf": _SUB_FUNCTION},
    ):
        c, o = _hex(cid), _hex(org_id)
        if c is not None and o is not None:
            carriers.add((o, c))

    candidates = bind.execute(
        sa.text(
            "SELECT id, organization_id, control_ids_used, controls_snapshot "
            "FROM risk_analysis_runs WHERE status = 'completed' AND is_stale = :f"
        ),
        {"f": False},
    ).fetchall()
    now = datetime.now(UTC).isoformat(sep=" ")
    flagged = 0
    for run_id, org_id, raw_ids, raw_snapshot in candidates:
        if not run_is_affected(raw_snapshot, raw_ids, _hex(org_id), carriers):
            continue
        bind.execute(
            sa.text("UPDATE risk_analysis_runs SET is_stale = :t WHERE id = :id"),
            {"t": True, "id": run_id},
        )
        bind.execute(
            sa.text(
                "INSERT INTO audit_log "
                "(id, organization_id, entity_type, entity_id, user_id, action, changes, timestamp) "
                "VALUES (:id, :org, 'risk_analysis_run', :eid, NULL, :action, :ch, :ts)"
            ),
            {
                "id": uuid.uuid4().hex,
                "org": org_id,
                "eid": run_id,
                "action": _ACTION,
                "ch": json.dumps({"is_stale": [False, True], "reason": ["", _REASON]}),
                "ts": now,
            },
        )
        flagged += 1

    logger.info(
        "issue-199 c8 stale runs: controls=%d flagged=%d",
        len({c for _, c in carriers}),
        flagged,
    )


def downgrade() -> None:
    """Documented no-op: staleness is advisory and the audit rows preserve the transition."""
