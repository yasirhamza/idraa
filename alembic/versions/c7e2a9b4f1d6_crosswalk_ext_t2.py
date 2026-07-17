"""REVIEWED crosswalk-seed extensions for rollout tranche 2 (#437 rollout T2).

Revision ID: c7e2a9b4f1d6
Revises: f1a2b3c4d5e6
Create Date: 2026-07-01

Three REVIEWED RiskFlow crosswalk-seed extensions add a genuine LEC channel to a
safeguard whose GENUINE library homes carry it on their own merits but which the
FAIR-Institute NIST CSF 1.1 / CIS 8.0 -> FAIR-CAM crosswalk (P2a) does not yet map
there. Each supersedes the FAIR-Institute channel reading for that safeguard ONLY;
the base mappings are left intact. Rationale + per-extension collateral checks live
inline in the ``citation.riskflow_extension`` field of each safeguard in
``data/seed_framework_crosswalk.json`` (the single source of truth the crosswalk gate
``tests/**/test_control_library_seed.py`` validates against).

  CIS 4.8  (Uninstall or Disable Unnecessary Services)  -> lec_prev_avoidance
           Grounds HAOS + HASS attack-surface-removal avoidance (rubric §4-I1:
           surface removal removes threat-contact vectors → TEF/Avoidance). Only
           HAOS + HASS carry 4.8, both T2 avoidance adds — no other collateral.
  CIS 14.2 (Train Workforce to Recognize Social Engineering) -> lec_prev_resistance
           Grounds PEPL human-vector resistance (trained user resists the
           social-engineering threat action → reduces Vulnerability). Only PEPL +
           SAT carry 14.2; SAT does not claim resistance — no collateral. Distinct
           from PEPL's earlier claim-drop, which rejected CONTRIVED PR.AC-1/PR.IP-3/
           CIS 16.10, not the genuine social-engineering-training home.
  CIS 16.1 (Secure Application Development Process) -> lec_prev_resistance
           Grounds SCP code-defect-density resistance (application is the ASSET;
           reducing exploitable-defect density lowers Vulnerability — rubric §4-I1
           Ex3). Carriers SCP/SSW/DAST/SAST; SSW already grounded via PR.PT-3
           (redundant), DAST/SAST do not claim resistance — no collateral.

The re-curation of the eight T2 library entries lands in a SEPARATE migration
(b8d3f6a1c4e7) so the P2a crosswalk and the P2b library stay in distinct migrations
(mirrors the T1 pair d4fc657eb424 / f1a2b3c4d5e6).

Idempotent + partial-DB safe: looks up each safeguard's ``framework_controls`` row
and inserts one ``framework_control_faircam`` link only if the safeguard exists and
the link is not already present (skips silently otherwise). No-hyphen
``uuid.uuid4().hex`` ids (raw-text-seed UUID foot-gun). Downgrade removes the links.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e2a9b4f1d6"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FRAMEWORK = "cis"
_FRAMEWORK_VERSION = "8.0"
# (safeguard code, FAIR-CAM function to add)
_EXTENSIONS = (
    ("4.8", "lec_prev_avoidance"),
    ("14.2", "lec_prev_resistance"),
    ("16.1", "lec_prev_resistance"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for code, function in _EXTENSIONS:
        control_id = bind.execute(
            sa.text(
                "SELECT id FROM framework_controls "
                "WHERE framework = :fw AND framework_version = :ver AND code = :code"
            ),
            {"fw": _FRAMEWORK, "ver": _FRAMEWORK_VERSION, "code": code},
        ).scalar()
        if control_id is None:
            # Safeguard absent (partial seed) — skip silently.
            continue
        already = bind.execute(
            sa.text(
                "SELECT 1 FROM framework_control_faircam "
                "WHERE framework_control_id = :cid AND fair_cam_function = :fn"
            ),
            {"cid": control_id, "fn": function},
        ).scalar()
        if already:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO framework_control_faircam "
                "(id, framework_control_id, fair_cam_function) "
                "VALUES (:id, :cid, :fn)"
            ),
            {"id": uuid.uuid4().hex, "cid": control_id, "fn": function},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for code, function in _EXTENSIONS:
        control_id = bind.execute(
            sa.text(
                "SELECT id FROM framework_controls "
                "WHERE framework = :fw AND framework_version = :ver AND code = :code"
            ),
            {"fw": _FRAMEWORK, "ver": _FRAMEWORK_VERSION, "code": code},
        ).scalar()
        if control_id is None:
            continue
        bind.execute(
            sa.text(
                "DELETE FROM framework_control_faircam "
                "WHERE framework_control_id = :cid AND fair_cam_function = :fn"
            ),
            {"cid": control_id, "fn": function},
        )
