# alembic/versions/b5e2c7a9d413_secondary_response_stakeholder_test.py
"""Issue #175: regulator-and-judgment reaction rule — response split across the
stakeholder boundary.

Converges the 29 touched library rows to data/seed_library_entries*.json (single
source of truth; the d3f1a7c9e5b2 pattern): 24 entries firing a ``fines`` share
(or, for one data_disclosure entry, a notification-bearing third-party-data
breach) now carry a ``response/secondary`` share — the notification, credit-
monitoring, investigation-cooperation and third-party legal-defence cost the
reaction forces — reclassified WITHIN the existing response budget, so Σshares
and the inherent PL+SL mean are unchanged (PL falls, SL rises) on these library
nodes, which carry no ``max``; an adopted scenario holding a capacity cap on one
of the four catastrophic entries samples the truncated lognormal, whose mean is
concave in the split, so its inherent mean rises instead (register B5); five
``competitive_advantage`` rows gain a stakeholder-test justification (no numeric
change). See docs/reference/loss-form-share-rubric.md §4 and
fair-departures-register.md B5.

Adopted scenarios are NOT rewritten here. Wizard-adopted scenarios hold a
re-fit of the pair the wizard seeds from the entry (the Beta-PERT 5/95
quantiles, not the node), so their repair needs the pooling pipeline;
library-refreshed scenarios (``loss_pinning.refresh_loss_from_library``) hold
the entry's dict per refreshed field plus an org-minted ``max`` on lognormal
fields (each field taken independently from the entry or the org override). Runs already stored read the scenario's own columns
and do not change until the scenario is repaired, refreshed or re-adopted. The
read-only diagnostic ``scripts/sweep_library_secondary_response.py`` classifies
adoptions (copy-stale / pristine / current / stale / modified). Repair trigger:
if a deployment's sweep reports ANY ``pristine`` or ``copy-stale`` scenario, land
a separate repair migration (pristine: SME pair → new pair + node re-fit;
copy-stale: the entry's new node written into the matching fieldset only,
preserving the scenario's ``max``, which the repair re-validates against the
rewritten fieldset's new p95 (the SL p95 rises by Σs_new/Σs_old, up to 2.08×
on telecom); for a scenario carrying one refresh-minted cap on both fields
the old PL p95 already dominates every post-change p95 because Σp > Σs
before and after on all four catastrophic entries; ``scenario.repair_loss_split`` audit row,
row_version bump) — never inline here.

No ``row_version`` bump: no library-content migration bumps it and entries have
no optimistic-lock read (only overrides do). No audit_log row: library rows are
global, not org-scoped, matching every prior recalibration migration.

Downgrade: documented NO-OP (one-way convergence to JSON).

Revision ID: b5e2c7a9d413
Revises: ffed7c509563
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "b5e2c7a9d413"
down_revision = "ffed7c509563"
branch_labels = None
depends_on = None

TOUCHED_SLUGS: frozenset[str] = frozenset(
    {
        # 24 renumbered (regulator-and-judgment reaction rule)
        "ransomware-on-ehr",
        "ransomware-on-historian",
        "unauthorized-plc-modification",
        "safety-system-bypass",
        "insider-data-theft-financial",
        "ransomware-healthcare-small-practice",
        "data-breach-notification-regulatory-tail",
        "generative-ai-prompt-injection",
        "chemical-process-safety-attack",
        "accidental-insider-exposure",
        "education-student-records-insider",
        "healthcare-staff-credential-phish",
        "food-recall-data-tampering",
        "financial-transaction-tampering",
        "healthcare-record-alteration",
        "tolling-plant-ransomware-customer-liability",
        "pipeline-nomination-scada-curtailment-shipper-penalty",
        "energy-settlement-platform-tampering-offtaker-liability",
        "law-enforcement-records-extortion-breach",
        "law-firm-privileged-data-ransomware-extortion",
        "k12-edtech-vendor-breach",
        "judiciary-court-system-ransomware",
        "edge-ransomware-perimeter-gateway",
        "telecom-lawful-intercept-nationstate-compromise",
        # 5 justified competitive_advantage placements (basis text only)
        "insider-ip-theft-manufacturing",
        "ip-theft-by-competitor",
        "crop-science-ip-exfiltration",
        "education-research-ip-exfiltration",
        "competitor-trade-secret-recruit",
    }
)


def _seed_paths() -> tuple[Path, Path]:
    # Verbatim from alembic/versions/d3f1a7c9e5b2_recalibrate_envelope_share_d_iii_a.py:30-45.
    import idraa

    try:
        root = Path(idraa.__file__).resolve().parent.parent.parent
        base = root / "data" / "seed_library_entries.json"
        ext = root / "data" / "seed_library_entries_extension.json"
        if base.exists() and ext.exists():
            return base, ext
    except Exception:  # pragma: no cover - fallback
        pass
    here = Path(__file__).resolve().parent.parent.parent
    return (
        here / "data" / "seed_library_entries.json",
        here / "data" / "seed_library_entries_extension.json",
    )


_UPDATE = sa.text(
    "UPDATE scenario_library_entries "
    "SET primary_loss = :primary_loss, "
    "    secondary_loss = :secondary_loss, "
    "    loss_form_profile = :loss_form_profile "
    "WHERE slug = :slug AND version = 1"
)


def upgrade() -> None:
    base_path, ext_path = _seed_paths()
    entries = json.loads(base_path.read_text(encoding="utf-8")) + json.loads(
        ext_path.read_text(encoding="utf-8")
    )
    by_slug = {e["slug"]: e for e in entries}
    missing = TOUCHED_SLUGS - by_slug.keys()
    if missing:
        raise RuntimeError(f"seed JSON lacks touched slugs: {sorted(missing)}")
    bind = op.get_bind()
    for slug in sorted(TOUCHED_SLUGS):
        e = by_slug[slug]
        bind.execute(
            _UPDATE,
            {
                "primary_loss": json.dumps(e["primary_loss"]),
                "secondary_loss": json.dumps(e.get("secondary_loss")),
                "loss_form_profile": json.dumps(e.get("loss_form_profile", [])),
                "slug": slug,
            },
        )


def downgrade() -> None:
    """Documented NO-OP: one-way convergence to the seed JSON (source of truth)."""
