"""backfill_calibration_anchors

Issue #103 — PR γ-3 (curation of all 31 library entries).

Backfills ``calibration_anchor`` on every published library entry row by
slug, sourcing the (industry, revenue_tier) values from the updated
``data/seed_library_entries.json``. Industry is advisory only (IRIS
Table 1 is industry-aggregate); revenue_tier drives the multiplier.

The 31 anchors were curated by reading each entry's example_incidents +
canonical_fair_gap to infer the curator's implicit reference tier:
- Enterprise-cited incidents (Norsk Hydro, TRISIS, JBS, Capital One,
  SolarWinds, MOVEit, BA/Marriott) → 10b_to_100b (IRIS bucket more_than_10b).
- Mid-large cited incidents (Kaseya, LAUSD, MOVEit median) → 1b_to_10b.
- Mid-market / mid-tier cited incidents (BEC patterns, S3 misconfig
  population) → 100m_to_1b.
- SMB-scoped entries (applicable_org_sizes = small/medium) → 10m_to_100m.
- Sub-$10M entries (small healthcare practices) → less_than_10m.

After this migration, every entry has a non-NULL anchor. A follow-up PR
flips the column to NOT NULL (PR γ-4) so the legacy "no-anchor" branch
in ``library_calibrated_pre_fill`` can be deleted.

Revision ID: d4f8a91c2e30
Revises: 2bb61838bc75
Create Date: 2026-05-13 21:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f8a91c2e30"
down_revision: str | None = "2bb61838bc75"
branch_labels: str | None = None
depends_on: str | None = None


# Curated anchors per spec §8.2 (PR γ-2 plan + γ-3 curation pass).
# Source of truth is data/seed_library_entries.json; this dict mirrors it
# for the in-migration UPDATE so the migration is self-contained (does not
# need to re-parse the seed JSON, which may evolve over time).
_ANCHORS: dict[str, dict[str, str]] = {
    "ransomware-on-ehr": {"industry": "healthcare", "revenue_tier": "1b_to_10b"},
    "ransomware-on-historian": {
        "industry": "utilities",
        "revenue_tier": "10b_to_100b",
    },
    "unauthorized-plc-modification": {
        "industry": "manufacturing",
        "revenue_tier": "10b_to_100b",
    },
    "safety-system-bypass": {
        "industry": "manufacturing",
        "revenue_tier": "10b_to_100b",
    },
    "denial-of-control": {"industry": "utilities", "revenue_tier": "10b_to_100b"},
    "hmi-credential-compromise": {
        "industry": "utilities",
        "revenue_tier": "100m_to_1b",
    },
    "it-ot-bridge-compromise": {
        "industry": "utilities",
        "revenue_tier": "10b_to_100b",
    },
    "nation-state-ics-supply-chain": {
        "industry": "utilities",
        "revenue_tier": "1b_to_10b",
    },
    "hacktivist-ot-disruption": {
        "industry": "utilities",
        "revenue_tier": "100m_to_1b",
    },
    "bec-fraud-financial": {"industry": "financial", "revenue_tier": "100m_to_1b"},
    "ransomware-on-virtualization-stack": {
        "industry": "manufacturing",
        "revenue_tier": "10b_to_100b",
    },
    "insider-data-theft-financial": {
        "industry": "financial",
        "revenue_tier": "10b_to_100b",
    },
    "insider-ip-theft-manufacturing": {
        "industry": "manufacturing",
        "revenue_tier": "10b_to_100b",
    },
    "cloud-account-takeover": {
        "industry": "information",
        "revenue_tier": "10b_to_100b",
    },
    "api-key-leak-devops": {"industry": "information", "revenue_tier": "100m_to_1b"},
    "ddos-extortion-financial": {
        "industry": "financial",
        "revenue_tier": "100m_to_1b",
    },
    "solarwinds-class-supply-chain": {
        "industry": "information",
        "revenue_tier": "10b_to_100b",
    },
    "moveit-class-zero-day-mft": {
        "industry": "professional",
        "revenue_tier": "1b_to_10b",
    },
    "session-hijack-post-mfa-bypass": {
        "industry": "information",
        "revenue_tier": "1b_to_10b",
    },
    "watering-hole-industry-targeted": {
        "industry": "utilities",
        "revenue_tier": "1b_to_10b",
    },
    "s3-misconfiguration-data-exposure": {
        "industry": "information",
        "revenue_tier": "100m_to_1b",
    },
    "package-registry-supply-chain": {
        "industry": "information",
        "revenue_tier": "100m_to_1b",
    },
    "ddos-financial-seasonal-peak": {
        "industry": "financial",
        "revenue_tier": "1b_to_10b",
    },
    "phishing-ad-compromise-ransomware": {
        "industry": "education",
        "revenue_tier": "1b_to_10b",
    },
    "ransomware-on-fileshare": {
        "industry": "professional",
        "revenue_tier": "10m_to_100m",
    },
    "credential-stuffing-consumer-portal": {
        "industry": "retail",
        "revenue_tier": "1b_to_10b",
    },
    "mfa-fatigue-prompt-bombing": {
        "industry": "information",
        "revenue_tier": "10b_to_100b",
    },
    "ransomware-healthcare-small-practice": {
        "industry": "healthcare",
        "revenue_tier": "less_than_10m",
    },
    "ot-network-scanning-reconnaissance": {
        "industry": "utilities",
        "revenue_tier": "1b_to_10b",
    },
    "data-breach-notification-regulatory-tail": {
        "industry": "retail",
        "revenue_tier": "10b_to_100b",
    },
    "generative-ai-prompt-injection": {
        "industry": "information",
        "revenue_tier": "1b_to_10b",
    },
}


def upgrade() -> None:
    bind = op.get_bind()
    for slug, anchor in _ANCHORS.items():
        bind.execute(
            sa.text(
                "UPDATE scenario_library_entries "
                "SET calibration_anchor = :anchor "
                "WHERE slug = :slug AND version = 1"
            ),
            {"anchor": json.dumps(anchor), "slug": slug},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE scenario_library_entries "
            "SET calibration_anchor = NULL "
            "WHERE version = 1"
        )
    )
