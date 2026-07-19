"""Recalibrate canonical frequency bands to register loss-event semantics.

Owner-approved 2026-07-19 after first prod UAT: under the D3 encoding a
register likelihood IS the loss-event frequency, and the original
domain-neutral log-decade scale (up to 250/yr) described threat-event
rates, not plausible loss-event recurrence — "Likely" bound positionally
landed on 10-100/yr. New scale anchors on once-in-X-years matrix
semantics: 0.01-0.1 / 0.1-0.5 / 0.5-2 / 2-10 / 10-50 events/yr, modes =
2sf geometric midpoints. Values are re-read from
data/seed_qualitative_bands.json so DB rows and the seed converge by
construction (precedent: 3d7b9e357d52). version bumps 1 -> 2 on the five
frequency rows so conversion_metadata.mapping_versions provenance
distinguishes pre/post-recalibration conversions. Magnitude bands
untouched (O-RA-cited). Downgrade is a deliberate no-op: the prior scale
lives in git history, not dual-sourced here.

Revision ID: 26444158e537
Revises: 32affcd5ec64
Create Date: 2026-07-19
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import idraa

# revision identifiers, used by Alembic.
revision: str = "26444158e537"
down_revision: Union[str, Sequence[str], None] = "32affcd5ec64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    seed_path = (
        Path(idraa.__file__).resolve().parent.parent.parent / "data" / "seed_qualitative_bands.json"
    )
    if not seed_path.exists():  # fallback anchor, mirrors e6882513a026
        seed_path = (
            Path(__file__).resolve().parent.parent.parent / "data" / "seed_qualitative_bands.json"
        )
    bands = json.loads(seed_path.read_text(encoding="utf-8"))
    bind = op.get_bind()
    for b in bands:
        if b["kind"] != "frequency":
            continue
        bind.execute(
            sa.text(
                "UPDATE qualitative_mapping_bands "
                "SET low = :low, mode = :mode, high = :high, "
                "    derivation = :derivation, version = 2 "
                "WHERE kind = 'frequency' AND label = :label"
            ),
            {
                "low": b["low"],
                "mode": b["mode"],
                "high": b["high"],
                "derivation": b["derivation"],
                "label": b["label"],
            },
        )


def downgrade() -> None:
    # Deliberate no-op (precedent 3d7b9e357d52): the pre-recalibration scale
    # is git-history-only — restoring it here would dual-source calibration
    # values. version stays 2.
    pass
