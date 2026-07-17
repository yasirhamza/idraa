"""REVIEWED crosswalk-seed extension: CIS 7.3/7.4 -> lec_prev_resistance (#437 rollout T1).

Revision ID: f1a2b3c4d5e6
Revises: d4fc657eb424
Create Date: 2026-07-01

The FAIR-Institute NIST CSF 1.1 / CIS 8.0 -> FAIR-CAM crosswalk (P2a) maps the two
patch-management safeguards CIS 7.3 (Perform Automated Operating System Patch
Management) and CIS 7.4 (Perform Automated Application Patch Management) to
``vmc_corr_implementation`` ONLY. RiskFlow's rubric §4-I1 Example 3 assigns patching
an OS/app CVE to LEC Resistance; this extension therefore adds ``lec_prev_resistance``
to those two safeguards' FAIR-CAM functions so Patch Management (PMGT) is grounded by
its GENUINE framework homes rather than the reverse-selected CIS 4.6 tag-to-score graft
(now removed from PMGT).

This is a METHODOLOGY DECISION (#437 rollout T1), documented inline here and in the
``citation.riskflow_extension`` field of the CIS 7.3/7.4 entries in
``data/seed_framework_crosswalk.json`` (the single source of truth the crosswalk gate
``tests/**/test_control_library_seed.py`` validates against). It deliberately
supersedes the FAIR-Institute crosswalk's patch -> VMC-Correction reading for channel
assignment; the base ``vmc_corr_implementation`` mapping is left intact.

Idempotent + partial-DB safe: looks up each safeguard's ``framework_controls`` row and
inserts one ``framework_control_faircam`` link only if the safeguard exists and the
link is not already present (skips silently otherwise — mirrors the pilot policy for
partially-seeded DBs). No-hyphen ``uuid.uuid4().hex`` ids (raw-text-seed UUID foot-gun).
Downgrade removes the two extension links.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "d4fc657eb424"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FRAMEWORK = "cis"
_FRAMEWORK_VERSION = "8.0"
_CODES = ("7.3", "7.4")
_FUNCTION = "lec_prev_resistance"


def upgrade() -> None:
    bind = op.get_bind()
    for code in _CODES:
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
            {"cid": control_id, "fn": _FUNCTION},
        ).scalar()
        if already:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO framework_control_faircam "
                "(id, framework_control_id, fair_cam_function) "
                "VALUES (:id, :cid, :fn)"
            ),
            {"id": uuid.uuid4().hex, "cid": control_id, "fn": _FUNCTION},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for code in _CODES:
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
            {"cid": control_id, "fn": _FUNCTION},
        )
