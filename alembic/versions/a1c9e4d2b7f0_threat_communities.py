"""threat communities: canonical Threat Agent Library + FK backfill

Revision ID: a1c9e4d2b7f0
Revises: f6a2b0d4e8c3
Create Date: 2026-09-30

Spec docs/superpowers/specs/2026-09-30-threat-agent-library-design.md Sec 4.

Order: (0) read-only _preflight maps every entry and scenario BEFORE any DDL (aiosqlite commits
DDL eagerly; a raise after CREATE TABLE would strand an empty table at the old revision and
boot-loop the machine); (1) DROP TABLE IF EXISTS threat_communities -- at this revision it can
only be such a leftover -- then create + seed nine rows (deterministic uuid5 ids, typed columns,
tcap_landmark JSON(none_as_null=True) so the non-malicious row is SQL NULL); (2) entries: add FK
cols, backfill from the FROZEN _ENTRY_COMMUNITY map (imported/unknown slugs -> _LEGACY_MAP), NOT
NULL, drop the named CHECK `threatactortype` (b8e0334b7f43 create_constraint=True) then the
column; (3) scenarios: add nullable FK cols + provenance; backfill (pin followed only when the
scenario's own legacy value equals the entry's; insider_malicious -> privileged_insider placeholder
with 'migrated_split_default'; NULL -> 'unassigned'); both-or-neither CHECK; drop CHECK + column;
(4) scoped PRAGMA foreign_key_check per table raising RuntimeError.

Frozen in-file (never imported from live code): _V1_COLUMNS, _LEGACY_MAP, _REVERSE, _ENTRY_COMMUNITY,
_ENTRY_LEGACY -- so later seed-schema growth never breaks a fresh `upgrade head`. The seed rows ARE
validated through the LIVE ThreatCommunitySeed (the attack-catalog precedent): a later REQUIRED
schema field would break this migration on a fresh database -- add new fields with defaults, or
freeze a copy of the schema here when that day comes. Every row of seed_threat_communities.json is
inserted as version 1: v2+ rows must live in a SEPARATE file.
No audit rows are written (vuln_framing precedent). Legacy insider_accidental scenarios migrate
as 'migrated' although their TEF was elicited under the old "try to compromise" wording -- no
numeric effect (the help's counting-rule section says the same).

Downgrade restores seed entries' exact legacy values from _ENTRY_LEGACY, maps others through
_REVERSE (both malicious insiders and third_party -> insider_malicious; opportunistic_hackers ->
cybercriminals, logged), recreates BOTH `threatactortype` CHECKs and the entries NOT NULL, so
up -> down -> up -> down is structurally identical to base.

Manual recovery if an upgrade failed after the DDL: DROP TABLE threat_communities; then rerun.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "a1c9e4d2b7f0"
down_revision: str | Sequence[str] | None = "f6a2b0d4e8c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

_V1_COLUMNS = (
    "slug",
    "name",
    "summary",
    "origin",
    "intent",
    "motive",
    "primary_intent",
    "sponsorship",
    "preferred_target_characteristics",
    "preferred_targets",
    "capability",
    "personal_risk_tolerance",
    "collateral_damage_concern",
    "threat_event_definition",
    "tef_basis",
    "reference_org",
    "tef_landmark",
    "tcap_landmark",
    "rationale",
    "citations",
    "reviewed_at",
)
_LEGACY_MAP = {
    "cybercriminals": "cybercriminals",
    "nation_state": "nation_state",
    "hacktivists": "hacktivists",
    "competitors": "competitors",
    "insider_accidental": "insider_accidental",
    "insider_malicious": "privileged_insider",
}
_SPLIT_VALUE = "insider_malicious"
_REVERSE = {
    "nation_state": "nation_state",
    "cybercriminals": "cybercriminals",
    "hacktivists": "hacktivists",
    "competitors": "competitors",
    "insider_accidental": "insider_accidental",
    "privileged_insider": "insider_malicious",
    "nonprivileged_insider": "insider_malicious",
    "third_party": "insider_malicious",
    "opportunistic_hackers": "cybercriminals",
}
_LOSSY = {"nonprivileged_insider", "third_party", "opportunistic_hackers"}
_CHECK_SQL = (
    "threat_actor_type IN ('cybercriminals', 'nation_state', 'insider_malicious', "
    "'insider_accidental', 'hacktivists', 'competitors')"
)  # spacing matches b8e0334b7f43's DDL text
_ENTRY_COMMUNITY: dict[
    str, str
] = {  # frozen copy of data/seed_library_entries*.json `threat_community` (Task 1)
    "ransomware-on-ehr": "cybercriminals",
    "ransomware-on-historian": "cybercriminals",
    "unauthorized-plc-modification": "nation_state",
    "safety-system-bypass": "nation_state",
    "denial-of-control": "nation_state",
    "hmi-credential-compromise": "cybercriminals",
    "it-ot-bridge-compromise": "cybercriminals",
    "nation-state-ics-supply-chain": "nation_state",
    "hacktivist-ot-disruption": "hacktivists",
    "bec-fraud-financial": "cybercriminals",
    "ransomware-on-virtualization-stack": "cybercriminals",
    "insider-data-theft-financial": "privileged_insider",
    "insider-ip-theft-manufacturing": "privileged_insider",
    "cloud-account-takeover": "cybercriminals",
    "api-key-leak-devops": "cybercriminals",
    "ddos-extortion-financial": "cybercriminals",
    "solarwinds-class-supply-chain": "nation_state",
    "moveit-class-zero-day-mft": "cybercriminals",
    "session-hijack-post-mfa-bypass": "cybercriminals",
    "watering-hole-industry-targeted": "nation_state",
    "s3-misconfiguration-data-exposure": "opportunistic_hackers",
    "package-registry-supply-chain": "nation_state",
    "ddos-financial-seasonal-peak": "cybercriminals",
    "phishing-ad-compromise-ransomware": "cybercriminals",
    "ransomware-on-fileshare": "cybercriminals",
    "credential-stuffing-consumer-portal": "cybercriminals",
    "mfa-fatigue-prompt-bombing": "cybercriminals",
    "ransomware-healthcare-small-practice": "cybercriminals",
    "ot-network-scanning-reconnaissance": "nation_state",
    "data-breach-notification-regulatory-tail": "cybercriminals",
    "generative-ai-prompt-injection": "cybercriminals",
    "ransomware-on-control-layer": "cybercriminals",
    "process-view-manipulation": "nation_state",
    "field-instrument-spoofing": "nation_state",
    "oem-remote-maintenance-abuse": "cybercriminals",
    "grid-protective-relay-manipulation": "nation_state",
    "pipeline-scada-integrity": "nation_state",
    "chemical-process-safety-attack": "nation_state",
    "accidental-insider-exposure": "insider_accidental",
    "web-app-exploitation": "cybercriminals",
    "third-party-processor-breach": "cybercriminals",
    "retail-pos-card-skimming": "cybercriminals",
    "public-sector-targeted-intrusion": "cybercriminals",
    "logistics-disruption": "cybercriminals",
    "telecom-subscriber-data-breach": "cybercriminals",
    "hospitality-pos-card-skimming": "cybercriminals",
    "hospitality-loyalty-account-takeover": "cybercriminals",
    "hospitality-guest-data-insider": "privileged_insider",
    "education-student-records-insider": "nonprivileged_insider",
    "gov-citizen-portal-ddos": "hacktivists",
    "gov-records-tampering": "privileged_insider",
    "gov-employee-insider-leak": "nonprivileged_insider",
    "ip-theft-by-competitor": "competitors",
    "manufacturing-billing-fraud": "nonprivileged_insider",
    "healthcare-staff-credential-phish": "cybercriminals",
    "professional-payroll-bec": "cybercriminals",
    "energy-billing-system-tamper": "cybercriminals",
    "telecom-ddos-core-network": "cybercriminals",
    "telecom-sim-swap-fraud": "cybercriminals",
    "telecom-bgp-route-hijack": "nation_state",
    "telecom-field-cabinet-tamper": "cybercriminals",
    "food-cold-chain-ransomware": "cybercriminals",
    "food-recall-data-tampering": "privileged_insider",
    "agri-equipment-physical-tamper": "hacktivists",
    "agri-coop-bec-fraud": "cybercriminals",
    "crop-science-ip-exfiltration": "competitors",
    "hospitality-booking-ddos-peak-season": "cybercriminals",
    "education-research-ip-exfiltration": "nation_state",
    "logistics-tms-data-tampering": "cybercriminals",
    "logistics-warehouse-physical-intrusion": "cybercriminals",
    "competitor-trade-secret-recruit": "competitors",
    "datacenter-physical-breach": "cybercriminals",
    "branch-atm-physical-tamper": "cybercriminals",
    "financial-transaction-tampering": "privileged_insider",
    "healthcare-record-alteration": "nonprivileged_insider",
    "retail-ecommerce-checkout-ddos": "cybercriminals",
    "saas-revenue-outage-sabotage": "privileged_insider",
    "professional-office-physical-theft": "cybercriminals",
    "retail-store-employee-fraud": "nonprivileged_insider",
    "manufacturing-facility-sabotage": "nonprivileged_insider",
    "financial-call-center-social-eng": "cybercriminals",
    "education-campus-facility-tamper": "hacktivists",
    "tolling-plant-ransomware-customer-liability": "cybercriminals",
    "pipeline-nomination-scada-curtailment-shipper-penalty": "nation_state",
    "energy-settlement-platform-tampering-offtaker-liability": "nation_state",
    "physician-practice-clearinghouse-revenue-disruption": "cybercriminals",
    "law-enforcement-records-extortion-breach": "cybercriminals",
    "casino-ransomware-operational-disruption": "cybercriminals",
    "telecom-lawful-intercept-nationstate-compromise": "nation_state",
    "law-firm-privileged-data-ransomware-extortion": "cybercriminals",
    "k12-edtech-vendor-breach": "cybercriminals",
    "higher-ed-insider-ddos": "nonprivileged_insider",
    "judiciary-court-system-ransomware": "cybercriminals",
    "edge-ransomware-perimeter-gateway": "cybercriminals",
    "edge-espionage-nationstate": "nation_state",
    "edge-device-orb-foothold": "nation_state",
    "transient-cyber-asset-ot-intrusion": "cybercriminals",
    "browser-zeroday-driveby": "cybercriminals",
    "email-client-zeroclick-espionage": "nation_state",
    "removable-media-airgap-ot": "nation_state",
    "ot-wireless-field-network-compromise": "cybercriminals",
    "destructive-wiper-nationstate": "nation_state",
}
_ENTRY_LEGACY: dict[
    str, str
] = {  # frozen copy of the JSON's `threat_actor_type`, for exact downgrade of seed rows
    "ransomware-on-ehr": "cybercriminals",
    "ransomware-on-historian": "cybercriminals",
    "unauthorized-plc-modification": "nation_state",
    "safety-system-bypass": "nation_state",
    "denial-of-control": "nation_state",
    "hmi-credential-compromise": "cybercriminals",
    "it-ot-bridge-compromise": "cybercriminals",
    "nation-state-ics-supply-chain": "nation_state",
    "hacktivist-ot-disruption": "hacktivists",
    "bec-fraud-financial": "cybercriminals",
    "ransomware-on-virtualization-stack": "cybercriminals",
    "insider-data-theft-financial": "insider_malicious",
    "insider-ip-theft-manufacturing": "insider_malicious",
    "cloud-account-takeover": "cybercriminals",
    "api-key-leak-devops": "cybercriminals",
    "ddos-extortion-financial": "cybercriminals",
    "solarwinds-class-supply-chain": "nation_state",
    "moveit-class-zero-day-mft": "cybercriminals",
    "session-hijack-post-mfa-bypass": "cybercriminals",
    "watering-hole-industry-targeted": "nation_state",
    "s3-misconfiguration-data-exposure": "cybercriminals",
    "package-registry-supply-chain": "nation_state",
    "ddos-financial-seasonal-peak": "cybercriminals",
    "phishing-ad-compromise-ransomware": "cybercriminals",
    "ransomware-on-fileshare": "cybercriminals",
    "credential-stuffing-consumer-portal": "cybercriminals",
    "mfa-fatigue-prompt-bombing": "cybercriminals",
    "ransomware-healthcare-small-practice": "cybercriminals",
    "ot-network-scanning-reconnaissance": "nation_state",
    "data-breach-notification-regulatory-tail": "cybercriminals",
    "generative-ai-prompt-injection": "cybercriminals",
    "ransomware-on-control-layer": "cybercriminals",
    "process-view-manipulation": "nation_state",
    "field-instrument-spoofing": "nation_state",
    "oem-remote-maintenance-abuse": "cybercriminals",
    "grid-protective-relay-manipulation": "nation_state",
    "pipeline-scada-integrity": "nation_state",
    "chemical-process-safety-attack": "nation_state",
    "accidental-insider-exposure": "insider_accidental",
    "web-app-exploitation": "cybercriminals",
    "third-party-processor-breach": "cybercriminals",
    "retail-pos-card-skimming": "cybercriminals",
    "public-sector-targeted-intrusion": "cybercriminals",
    "logistics-disruption": "cybercriminals",
    "telecom-subscriber-data-breach": "cybercriminals",
    "hospitality-pos-card-skimming": "cybercriminals",
    "hospitality-loyalty-account-takeover": "cybercriminals",
    "hospitality-guest-data-insider": "insider_malicious",
    "education-student-records-insider": "insider_malicious",
    "gov-citizen-portal-ddos": "hacktivists",
    "gov-records-tampering": "insider_malicious",
    "gov-employee-insider-leak": "insider_malicious",
    "ip-theft-by-competitor": "competitors",
    "manufacturing-billing-fraud": "insider_malicious",
    "healthcare-staff-credential-phish": "cybercriminals",
    "professional-payroll-bec": "cybercriminals",
    "energy-billing-system-tamper": "cybercriminals",
    "telecom-ddos-core-network": "cybercriminals",
    "telecom-sim-swap-fraud": "cybercriminals",
    "telecom-bgp-route-hijack": "nation_state",
    "telecom-field-cabinet-tamper": "cybercriminals",
    "food-cold-chain-ransomware": "cybercriminals",
    "food-recall-data-tampering": "insider_malicious",
    "agri-equipment-physical-tamper": "hacktivists",
    "agri-coop-bec-fraud": "cybercriminals",
    "crop-science-ip-exfiltration": "competitors",
    "hospitality-booking-ddos-peak-season": "cybercriminals",
    "education-research-ip-exfiltration": "nation_state",
    "logistics-tms-data-tampering": "cybercriminals",
    "logistics-warehouse-physical-intrusion": "cybercriminals",
    "competitor-trade-secret-recruit": "competitors",
    "datacenter-physical-breach": "cybercriminals",
    "branch-atm-physical-tamper": "cybercriminals",
    "financial-transaction-tampering": "insider_malicious",
    "healthcare-record-alteration": "insider_malicious",
    "retail-ecommerce-checkout-ddos": "cybercriminals",
    "saas-revenue-outage-sabotage": "insider_malicious",
    "professional-office-physical-theft": "cybercriminals",
    "retail-store-employee-fraud": "insider_malicious",
    "manufacturing-facility-sabotage": "insider_malicious",
    "financial-call-center-social-eng": "cybercriminals",
    "education-campus-facility-tamper": "hacktivists",
    "tolling-plant-ransomware-customer-liability": "cybercriminals",
    "pipeline-nomination-scada-curtailment-shipper-penalty": "nation_state",
    "energy-settlement-platform-tampering-offtaker-liability": "nation_state",
    "physician-practice-clearinghouse-revenue-disruption": "cybercriminals",
    "law-enforcement-records-extortion-breach": "cybercriminals",
    "casino-ransomware-operational-disruption": "cybercriminals",
    "telecom-lawful-intercept-nationstate-compromise": "nation_state",
    "law-firm-privileged-data-ransomware-extortion": "cybercriminals",
    "k12-edtech-vendor-breach": "cybercriminals",
    "higher-ed-insider-ddos": "insider_malicious",
    "judiciary-court-system-ransomware": "cybercriminals",
    "edge-ransomware-perimeter-gateway": "cybercriminals",
    "edge-espionage-nationstate": "nation_state",
    "edge-device-orb-foothold": "nation_state",
    "transient-cyber-asset-ot-intrusion": "cybercriminals",
    "browser-zeroday-driveby": "cybercriminals",
    "email-client-zeroclick-espionage": "nation_state",
    "removable-media-airgap-ot": "nation_state",
    "ot-wireless-field-network-compromise": "cybercriminals",
    "destructive-wiper-nationstate": "nation_state",
}

_TC_TABLE = sa.table(
    "threat_communities",
    sa.column("id", sa.Uuid()),
    sa.column("version", sa.Integer()),
    sa.column("slug", sa.String()),
    sa.column("source", sa.String()),
    sa.column("name", sa.String()),
    sa.column("summary", sa.Text()),
    sa.column("origin", sa.String()),
    sa.column("intent", sa.String()),
    sa.column("motive", sa.Text()),
    sa.column("primary_intent", sa.Text()),
    sa.column("sponsorship", sa.Text()),
    sa.column("preferred_target_characteristics", sa.Text()),
    sa.column("preferred_targets", sa.Text()),
    sa.column("capability", sa.Text()),
    sa.column("personal_risk_tolerance", sa.Text()),
    sa.column("collateral_damage_concern", sa.Text()),
    sa.column("threat_event_definition", sa.Text()),
    sa.column("tef_basis", sa.Text()),
    sa.column("reference_org", sa.JSON()),
    sa.column("tef_landmark", sa.JSON()),
    sa.column("tcap_landmark", sa.JSON(none_as_null=True)),
    sa.column("rationale", sa.Text()),
    sa.column("citations", sa.JSON()),
    sa.column("reviewed_at", sa.Date()),
    sa.column("published_at", sa.DateTime(timezone=True)),
)


def _data_dir() -> Path:
    import idraa

    root = Path(idraa.__file__).resolve().parent.parent.parent / "data"
    if not root.exists():  # Major-finding F25 path fallback
        root = Path(__file__).resolve().parent.parent.parent / "data"
    return root


def _hex(v: object) -> str:
    return uuid.UUID(str(v)).hex


def _preflight(
    bind: sa.Connection,
) -> tuple[
    dict[str, str],
    dict[str, str | None],
    list[tuple[str, str | None, dict[str, object] | None]],
]:
    """Read-only. Returns (entry_community_by_hex, entry_legacy_by_hex, scenario_rows).

    Architect N7: ``scenario_rows``' third element is ``library_pin`` already PARSED from its
    stored JSON string (never the raw string) -- the ``json.loads`` used to live in the
    ``upgrade()`` backfill loop itself, so a malformed pin was only discovered mid-backfill,
    after the table-create + seed DDL had already run; aiosqlite commits DDL eagerly, so that
    raise would strand a partially-migrated table at the old revision and boot-loop the
    machine (same rationale as this function's existing checks). Parsing here instead means
    EVERY row is validated before any DDL executes.
    """
    entry_community: dict[str, str] = {}
    entry_legacy: dict[str, str | None] = {}
    for eid, _ver, eslug, tat in bind.execute(
        sa.text("SELECT id, version, slug, threat_actor_type FROM scenario_library_entries")
    ).all():
        community = _ENTRY_COMMUNITY.get(eslug) or _LEGACY_MAP.get(tat or "")
        if community is None:
            raise RuntimeError(
                f"pre-flight: library entry {eslug!r} has no community and unknown legacy value {tat!r}"
            )
        entry_community[_hex(eid)] = community
        entry_legacy[_hex(eid)] = tat
    raw_scenario_rows = bind.execute(
        sa.text("SELECT id, threat_actor_type, library_pin FROM scenarios")
    ).all()
    scenario_rows: list[tuple[str, str | None, dict[str, object] | None]] = []
    for sid, tat, pin_raw in raw_scenario_rows:
        if tat not in (None, "") and tat not in _LEGACY_MAP:
            raise RuntimeError(
                f"pre-flight: scenario {sid} has unknown legacy threat_actor_type {tat!r}"
            )
        if isinstance(pin_raw, str):
            try:
                pin = json.loads(pin_raw)
            except (json.JSONDecodeError, ValueError) as exc:
                raise RuntimeError(
                    f"pre-flight: scenario {sid} has a malformed library_pin: {exc}"
                ) from exc
        else:
            pin = pin_raw or None
        scenario_rows.append((str(sid), tat, pin))
    return entry_community, entry_legacy, scenario_rows


def upgrade() -> None:
    from idraa.models.threat_community import canonical_threat_community_id
    from idraa.schemas.threat_community import load_threat_community_seed, seed_to_row_kwargs

    bind = op.get_bind()
    entry_community_by_hex, entry_legacy_by_hex, scenario_rows = _preflight(bind)

    # ---- 1. table + seed ---------------------------------------------------
    op.execute("DROP TABLE IF EXISTS threat_communities")
    op.create_table(
        "threat_communities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="seed"),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("intent", sa.String(16), nullable=False),
        sa.Column("motive", sa.Text(), nullable=False),
        sa.Column("primary_intent", sa.Text(), nullable=False),
        sa.Column("sponsorship", sa.Text(), nullable=False),
        sa.Column("preferred_target_characteristics", sa.Text(), nullable=False),
        sa.Column("preferred_targets", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("personal_risk_tolerance", sa.Text(), nullable=False),
        sa.Column("collateral_damage_concern", sa.Text(), nullable=False),
        sa.Column("threat_event_definition", sa.Text(), nullable=False),
        sa.Column("tef_basis", sa.Text(), nullable=False),
        sa.Column("reference_org", sa.JSON(), nullable=False),
        sa.Column("tef_landmark", sa.JSON(), nullable=False),
        sa.Column("tcap_landmark", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("reviewed_at", sa.Date(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", "version", name="pk_threat_communities"),
        sa.UniqueConstraint("slug", "version", name="uq_threat_community_slug_version"),
    )
    op.create_index("ix_threat_communities_slug", "threat_communities", ["slug"])
    now = dt.datetime.now(dt.UTC)
    id_by_slug: dict[str, str] = {}
    for seed in load_threat_community_seed(_data_dir() / "seed_threat_communities.json"):
        cid = canonical_threat_community_id(seed.slug)
        id_by_slug[seed.slug] = cid.hex
        kwargs = seed_to_row_kwargs(seed)
        bind.execute(
            _TC_TABLE.insert().values(
                id=cid,
                version=1,
                source="seed",
                published_at=now,
                **{k: kwargs[k] for k in _V1_COLUMNS},
            )
        )

    # ---- 2. scenario_library_entries -----------------------------------
    op.add_column(
        "scenario_library_entries", sa.Column("threat_community_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "scenario_library_entries",
        sa.Column("threat_community_version", sa.Integer(), nullable=True),
    )
    for eid, ever, eslug, tat in bind.execute(
        sa.text("SELECT id, version, slug, threat_actor_type FROM scenario_library_entries")
    ).all():
        community = entry_community_by_hex[_hex(eid)]
        if eslug not in _ENTRY_COMMUNITY:
            log.info("threat_communities: imported entry %s mapped %s -> %s", eslug, tat, community)
        bind.execute(
            sa.text(
                "UPDATE scenario_library_entries SET threat_community_id=:c, threat_community_version=1 WHERE id=:id AND version=:v"
            ),
            {"c": id_by_slug[community], "id": eid, "v": ever},
        )
    with op.batch_alter_table("scenario_library_entries") as b:
        b.alter_column("threat_community_id", nullable=False)
        b.alter_column("threat_community_version", nullable=False)
        b.create_foreign_key(
            "fk_library_entry_threat_community",
            "threat_communities",
            ["threat_community_id", "threat_community_version"],
            ["id", "version"],
        )
        b.drop_index("ix_library_entry_threat_actor")
        b.create_index(
            "ix_library_entry_threat_community", ["threat_community_id", "threat_community_version"]
        )
        b.drop_constraint("threatactortype", type_="check")
        b.drop_column("threat_actor_type")

    # ---- 3. scenarios ---------------------------------------------------
    op.add_column("scenarios", sa.Column("threat_community_id", sa.Uuid(), nullable=True))
    op.add_column("scenarios", sa.Column("threat_community_version", sa.Integer(), nullable=True))
    op.add_column(
        "scenarios",
        sa.Column(
            "threat_community_provenance", sa.String(32), nullable=False, server_default="assigned"
        ),
    )
    for sid, tat, pin in scenario_rows:
        community_hex: str | None = None
        provenance = "unassigned"
        followed = False
        if isinstance(pin, dict) and pin.get("entry_id"):
            try:
                ehex = _hex(pin["entry_id"])
            except ValueError:
                ehex = None
            if (
                ehex in entry_community_by_hex
                and tat is not None
                and entry_legacy_by_hex.get(ehex) == tat
            ):
                community_hex, provenance, followed = (
                    id_by_slug[entry_community_by_hex[ehex]],
                    "migrated",
                    True,
                )
        if not followed and tat not in (None, ""):
            community_hex = id_by_slug[_LEGACY_MAP[tat]]
            provenance = "migrated_split_default" if tat == _SPLIT_VALUE else "migrated"
        bind.execute(
            sa.text(
                "UPDATE scenarios SET threat_community_id=:c, threat_community_version=:v, threat_community_provenance=:p WHERE id=:id"
            ),
            {"c": community_hex, "v": 1 if community_hex else None, "p": provenance, "id": sid},
        )
    with op.batch_alter_table("scenarios") as b:
        b.create_foreign_key(
            "fk_scenario_threat_community",
            "threat_communities",
            ["threat_community_id", "threat_community_version"],
            ["id", "version"],
        )
        b.create_index(
            "ix_scenarios_threat_community", ["threat_community_id", "threat_community_version"]
        )
        b.create_check_constraint(
            "ck_scenario_threat_community_pair",
            "(threat_community_id IS NULL) = (threat_community_version IS NULL)",
        )
        b.drop_constraint("threatactortype", type_="check")
        b.drop_column("threat_actor_type")

    # ---- 4. integrity (scoped; RuntimeError, never assert) -------------
    if bind.dialect.name == "sqlite":
        for t in ("scenarios", "scenario_library_entries"):
            bad = bind.execute(sa.text(f"PRAGMA foreign_key_check({t})")).all()
            if bad:
                raise RuntimeError(f"foreign_key_check({t}) failed: {bad[:5]}")


def downgrade() -> None:
    bind = op.get_bind()
    slug_by_hex = {
        _hex(i): s
        for i, s in bind.execute(sa.text("SELECT id, slug FROM threat_communities")).all()
    }

    with op.batch_alter_table("scenarios") as b:
        b.add_column(
            sa.Column("threat_actor_type", sa.String(18), nullable=True)
        )  # base width is VARCHAR(18)
    for sid, cid in bind.execute(
        sa.text(
            "SELECT id, threat_community_id FROM scenarios WHERE threat_community_id IS NOT NULL"
        )
    ).all():
        slug = slug_by_hex[_hex(cid)]
        if slug in _LOSSY:
            log.warning(
                "downgrade: scenario %s community %s -> %s (lossy)", sid, slug, _REVERSE[slug]
            )
        bind.execute(
            sa.text("UPDATE scenarios SET threat_actor_type=:t WHERE id=:id"),
            {"t": _REVERSE[slug], "id": sid},
        )
    with op.batch_alter_table("scenarios") as b:
        b.drop_constraint("ck_scenario_threat_community_pair", type_="check")
        b.drop_constraint("fk_scenario_threat_community", type_="foreignkey")
        b.drop_index("ix_scenarios_threat_community")
        b.drop_column("threat_community_provenance")
        b.drop_column("threat_community_version")
        b.drop_column("threat_community_id")
        b.create_check_constraint("threatactortype", _CHECK_SQL)

    with op.batch_alter_table("scenario_library_entries") as b:
        b.add_column(
            sa.Column("threat_actor_type", sa.String(18), nullable=True)
        )  # base width is VARCHAR(18)
    for eid, ever, eslug, cid, source in bind.execute(
        sa.text(
            "SELECT id, version, slug, threat_community_id, source FROM scenario_library_entries"
        )
    ).all():
        legacy = _ENTRY_LEGACY.get(eslug) if source == "seed" else None
        if legacy is None:
            legacy = _REVERSE[slug_by_hex[_hex(cid)]]
        bind.execute(
            sa.text(
                "UPDATE scenario_library_entries SET threat_actor_type=:t WHERE id=:id AND version=:v"
            ),
            {"t": legacy, "id": eid, "v": ever},
        )
    with op.batch_alter_table("scenario_library_entries") as b:
        b.alter_column("threat_actor_type", nullable=False)
        b.drop_constraint("fk_library_entry_threat_community", type_="foreignkey")
        b.drop_index("ix_library_entry_threat_community")
        b.create_index("ix_library_entry_threat_actor", ["threat_actor_type"])
        b.drop_column("threat_community_version")
        b.drop_column("threat_community_id")
        b.create_check_constraint("threatactortype", _CHECK_SQL)

    op.drop_index("ix_threat_communities_slug", table_name="threat_communities")
    op.drop_table("threat_communities")
