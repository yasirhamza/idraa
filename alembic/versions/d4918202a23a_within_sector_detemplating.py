"""#detemplating: within-sector de-templating content migration. Reads the seed
JSON (source of truth, D-iii-a d3f1a7c9e5b2 convergence pattern) and UPDATEs
threat_event_frequency / vulnerability / (secondary_loss + loss_form_profile)
for the touched slugs (union of the builder's TEF_NEW / VULN_NEW /
SL_SECONDARY dicts, scripts/build_within_sector_detemplating.py). Parameterized
binds. No-op downgrade.

Revision ID: d4918202a23a
Down revision: c8e2f1a4b6d3
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "d4918202a23a"
down_revision = "c8e2f1a4b6d3"
branch_labels = None
depends_on = None

_TEF_SLUGS = (
    "process-view-manipulation",
    "pipeline-nomination-scada-curtailment-shipper-penalty",
    "web-app-exploitation",
    "ddos-financial-seasonal-peak",
    "insider-data-theft-financial",
    "branch-atm-physical-tamper",
    "hospitality-pos-card-skimming",
    "hospitality-guest-data-insider",
    "manufacturing-billing-fraud",
    "food-recall-data-tampering",
    "manufacturing-facility-sabotage",
    "professional-payroll-bec",
    "professional-office-physical-theft",
    "s3-misconfiguration-data-exposure",
    "mfa-fatigue-prompt-bombing",
    "competitor-trade-secret-recruit",
    "saas-revenue-outage-sabotage",
    "telecom-bgp-route-hijack",
)
_VULN_SLUGS = (
    "ransomware-on-historian",
    "process-view-manipulation",
    "hmi-credential-compromise",
    "pipeline-nomination-scada-curtailment-shipper-penalty",
    "energy-settlement-platform-tampering-offtaker-liability",
    "it-ot-bridge-compromise",
    "nation-state-ics-supply-chain",
    "oem-remote-maintenance-abuse",
    "hacktivist-ot-disruption",
    "energy-billing-system-tamper",
    "watering-hole-industry-targeted",
    "insider-data-theft-financial",
    "public-sector-targeted-intrusion",
    "gov-employee-insider-leak",
    "unauthorized-plc-modification",
    "insider-ip-theft-manufacturing",
    "ransomware-on-control-layer",
    "ip-theft-by-competitor",
    "manufacturing-billing-fraud",
    "food-recall-data-tampering",
    "tolling-plant-ransomware-customer-liability",
    "data-breach-notification-regulatory-tail",
    "retail-store-employee-fraud",
    "api-key-leak-devops",
    "session-hijack-post-mfa-bypass",
    "solarwinds-class-supply-chain",
    "datacenter-physical-breach",
)
_SL_SLUGS = (
    "it-ot-bridge-compromise",
    "nation-state-ics-supply-chain",
    "energy-settlement-platform-tampering-offtaker-liability",
    "accidental-insider-exposure",
    "healthcare-staff-credential-phish",
    "safety-system-bypass",
    "chemical-process-safety-attack",
    "ransomware-on-virtualization-stack",
    "food-cold-chain-ransomware",
    "food-recall-data-tampering",
    "tolling-plant-ransomware-customer-liability",
    "cloud-account-takeover",
    "solarwinds-class-supply-chain",
    "session-hijack-post-mfa-bypass",
    "competitor-trade-secret-recruit",
    "saas-revenue-outage-sabotage",
    "package-registry-supply-chain",
    "mfa-fatigue-prompt-bombing",
    "telecom-bgp-route-hijack",
    "telecom-lawful-intercept-nationstate-compromise",
    "logistics-tms-data-tampering",
)

_UPDATE_TEF = sa.text(
    "UPDATE scenario_library_entries SET threat_event_frequency = :v "
    "WHERE slug = :slug AND version = 1"
)
_UPDATE_VULN = sa.text(
    "UPDATE scenario_library_entries SET vulnerability = :v WHERE slug = :slug AND version = 1"
)
_UPDATE_SL = sa.text(
    "UPDATE scenario_library_entries SET secondary_loss = :sl, loss_form_profile = :lfp "
    "WHERE slug = :slug AND version = 1"
)


def _seed() -> dict[str, dict]:
    def _paths(root: Path) -> list[Path]:
        return [
            root / "data" / n
            for n in ("seed_library_entries.json", "seed_library_entries_extension.json")
        ]

    paths: list[Path] | None = None
    try:  # primary: the installed idraa package root (D-iii-a d3f1a7c9e5b2 pattern)
        import idraa

        cand = _paths(Path(idraa.__file__).resolve().parent.parent.parent)
        if all(p.exists() for p in cand):
            paths = cand
    except Exception:  # pragma: no cover - fallback
        paths = None
    if paths is None:  # fallback: migration-file-relative repo root
        paths = _paths(Path(__file__).resolve().parent.parent.parent)
    rows: list[dict] = []
    for p in paths:
        rows.extend(json.loads(p.read_text(encoding="utf-8")))
    return {r["slug"]: r for r in rows}


def upgrade() -> None:
    seed = _seed()
    bind = op.get_bind()
    for slug in _TEF_SLUGS:
        bind.execute(
            _UPDATE_TEF,
            {"v": json.dumps(seed[slug]["threat_event_frequency"]), "slug": slug},
        )
    for slug in _VULN_SLUGS:
        bind.execute(
            _UPDATE_VULN,
            {"v": json.dumps(seed[slug]["vulnerability"]), "slug": slug},
        )
    for slug in _SL_SLUGS:
        e = seed[slug]
        bind.execute(
            _UPDATE_SL,
            {
                "sl": json.dumps(e["secondary_loss"]),
                "lfp": json.dumps(e["loss_form_profile"]),
                "slug": slug,
            },
        )


def downgrade() -> None:
    """No-op -- one-way content migration (D-iii-a d3f1a7c9e5b2 policy, ruling R6).
    Prior TEF/vuln/SL payloads are superseded and recoverable from git only."""
    pass
