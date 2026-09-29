# alembic/versions/e5f1a9c3d7b2_epic_f_library_curation.py
"""Epic F (#192): scenario-library curation content migration.

Converges the 38 (slug, column) cells changed by the Epic F campaign (Tasks
2-3: pilot label rows, ``people``-asset-class relabels per O2, three merges-
by-deprecation, four accepted sharpens, the fix-round-1 wording corrections,
and the accidental-insider-exposure threat_event_type relabel + #175 3/7
response split per O7) to ``data/seed_library_entries*.json`` -- the single
source of truth -- on any DB whose rows still hold the pre-campaign values.

Literal-table rationale: every changed cell is carried here as an explicit
``(slug, column, old, new)`` literal rather than re-read from the seed JSON
at migration-run time. This is deliberate dual-sourcing, not an oversight --
the ``old`` literal makes the change reviewable in the diff (a reader sees
exactly what a production row currently holds and what it becomes, without
cross-referencing git history) and lets ``upgrade()`` detect drift (a row
whose current value is neither ``old`` nor ``new`` was edited out-of-band
and must never be silently overwritten). ``tests/migrations/
test_epic_f_library_curation.py`` test (e) pins every ``new`` literal here
back to the current seed JSON, so a stale ``_CHANGES`` entry (someone edits
the JSON again without regenerating this table) fails loudly in CI rather
than shipping a migration that converges to an already-superseded value.
``scripts/build_epic_f_migration_table.py`` is the generator: it diffs the
working tree against ``git merge-base HEAD origin/main`` and prints this
table; re-run and re-paste it after any rebase that moves ``origin/main``.

Comparison semantics (spec §4.1.7), applied per ``(slug, column)`` cell:
  - current DB value == ``new`` (parsed-equal for JSON columns, exact for
    text/enum columns) -> "already": no-op, the cell already holds the
    target value (a second run of this migration, or a fresh DB whose
    earlier insert already read the edited seed JSON, both land here).
  - current DB value == ``old`` -> "apply": UPDATE to ``new`` (unless the
    cell's slug-group is poisoned by a sibling drift -- see below).
  - current DB value is neither -> "drift": the cell was changed out of
    band (a hotfix, a hand edit, a since-superseded prior migration).
A missing seed row (no ``slug``/``version=1``/``source='seed'`` match) is
also counted as drift, rather than raising -- an org that deleted or never
seeded a row must not block every other row's convergence.

**Per-slug atomicity (spec §4.1 amendment, methodology finding M4-1).**
``_CHANGES`` cells are grouped by slug (``_group_by_slug``) and classified as
a unit (``_classify_slug_group``) before any write: if ANY cell in a slug's
group is "drift" (or its row is missing), the WHOLE group is skipped -- an
"apply"-eligible cell in a poisoned group is counted and logged as "drift"
too, not applied, and only ONE warning is logged per poisoned slug, naming
the slug and the genuinely-drifted column(s) (never cell values). An
"already" cell is unaffected either way (it needs no write regardless of its
siblings). This applies to every slug uniformly, not only calibration-
coupled ones: ``accidental-insider-exposure``'s ``primary_loss``/
``secondary_loss``/``loss_form_profile``/``threat_event_type`` are one
calibration unit (the PL/SL envelope split by the profile's shares, per
register B5's within-budget-split-conserves-inherent-mean invariant), and a
partial convergence there would leave the entry's PL/SL split incoherent
with its own ``loss_form_profile`` shares even though each cell's own drift
check passed. Rather than special-case that one slug, every slug gets the
same atomic-group treatment -- simpler to reason about and a uniform
defense-in-depth for any future coupled cells. Downgrade mirrors this with
``old``/``new`` swapped (same grouping, same guard).

**Counter semantics: cells, not slugs.** The logged summary
(``applied=<A> already_new=<M> drift=<K>``, or ``already_old`` on downgrade)
counts *cells* (rows of ``_CHANGES``), not slugs: ``A + M + K`` always equals
``len(_CHANGES)``. Because of the atomic-group rule above, ``K`` (drift) can
exceed the number of cells that are themselves individually off-script -- a
single genuinely-drifted cell in a 4-cell slug poisons all 4, so all 4 count
toward ``drift`` even though only 1 differs from both ``old`` and ``new``.
The dry-run script (``scripts/check_epic_f_migration.py``) reports the same
way, using the same shared ``_group_by_slug``/``_classify_slug_group``
functions, so its counts and per-row report match what a real
``alembic upgrade`` would do.

Adopted rows (mirrors ``b5e2c7a9d413``'s adopted-rows paragraph): no
org-owned row (``scenarios``, ``scenario_library_overrides``, SME estimates)
is read or written by this migration -- only the canonical
``scenario_library_entries`` (``source='seed'``, ``version=1``) rows are
touched. A scenario cloned from ``accidental-insider-exposure`` before this
migration ran keeps its own stored loss values until the org refreshes it
(``loss_pinning.refresh_loss_from_library``) or re-estimates it; this
migration does not reach into org data to force that update.
``scripts/sweep_epic_f_adopted_rows.py --gate`` (read-only) is the repair
trigger for any adoption left stale by this campaign; a repair, if the
sweep finds one, lands as its own separate migration -- never folded into
this one.

``accidental-insider-exposure``'s ``primary_loss``/``secondary_loss``/
``loss_form_profile``/``threat_event_type`` cells are an in-place edit at
the entry's existing ``version = 1`` row (Sec-6): the #175 3/7 response
split changes what the pinned version's numbers resolve to without minting
a new version row, matching how every prior recalibration migration in this
library (e.g. ``b5e2c7a9d413``) has treated content-only convergence.

No ``row_version`` bump and no ``audit_log`` row: library entries are global
(not org-scoped) content, and no prior seed-content migration in this
history has bumped ``row_version`` or written an audit row for a library
UPDATE -- ``scenario_library_entries`` has no optimistic-lock read path,
only ``scenario_library_overrides`` does.

Downgrade mirrors upgrade with ``old``/``new`` swapped, same drift guard.

Revision ID: e5f1a9c3d7b2
Revises: 08e3f1cd45b8
"""

from __future__ import annotations

import json
import logging
from typing import Literal

import sqlalchemy as sa
from alembic import op

revision = "e5f1a9c3d7b2"
down_revision = "08e3f1cd45b8"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# set(ScenarioLibraryEntry.__table__.columns.keys()) & set(LibraryEntrySeed.model_fields)
# - {"slug"} (ORM <-> DTO field-sync rule; test (g) pins this equality). A frozen literal
# here, never a live ORM/DTO import -- a migration's behaviour must not shift if the ORM
# gains or loses a column after this migration has already shipped.
_ALLOWED_COLUMNS: frozenset[str] = frozenset(
    {
        "name",
        "status",
        "threat_event_type",
        "threat_actor_type",
        "asset_class",
        "attack_vector",
        "tags",
        "description",
        "example_incidents",
        "source_citations",
        "canonical_fair_gap",
        "applicable_industries",
        "applicable_sub_sectors",
        "applicable_org_sizes",
        "threat_event_frequency",
        "vulnerability",
        "primary_loss",
        "secondary_loss",
        "suggested_control_ids",
        "standards_references",
        "calibration_anchor",
        "loss_tier",
        "loss_shape",
        "loss_form_profile",
    }
)

# The subset of _ALLOWED_COLUMNS whose ORM column type is JSON (parsed-equal comparison,
# json.dumps/json.loads codec). The rest are text/enum columns (String/Text/Enum with
# native_enum=False) compared and written verbatim.
_JSON_COLUMNS: frozenset[str] = frozenset(
    {
        "tags",
        "source_citations",
        "applicable_industries",
        "applicable_sub_sectors",
        "applicable_org_sizes",
        "threat_event_frequency",
        "vulnerability",
        "primary_loss",
        "secondary_loss",
        "suggested_control_ids",
        "standards_references",
        "calibration_anchor",
        "loss_form_profile",
    }
)

# Generated by scripts/build_epic_f_migration_table.py (diff of
# data/seed_library_entries*.json between `git merge-base HEAD origin/main` and the
# working tree at the time this migration was written). 38 cells across the 24 Epic F
# slugs. Re-run and re-paste after any rebase that moves origin/main (A-5/S-13).
_CHANGES: tuple[tuple[str, str, object, object], ...] = (
    (
        "accidental-insider-exposure",
        "loss_form_profile",
        [
            {
                "form": "response",
                "kind": "primary",
                "magnitude_basis": "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md)",
                "citations": [],
                "verified": False,
                "composition_role": "dominant",
                "share": 0.12,
            },
            {
                "form": "reputation",
                "kind": "secondary",
                "magnitude_basis": "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md)",
                "citations": [],
                "verified": False,
                "composition_role": "dominant",
                "share": 0.12,
            },
            {
                "form": "fines",
                "kind": "secondary",
                "magnitude_basis": "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md)",
                "citations": [],
                "verified": False,
                "composition_role": "contributing",
                "share": 0.1,
            },
            {
                "form": "response",
                "kind": "secondary",
                "magnitude_basis": "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md §4 regulator-and-judgment reaction rule): notification, credit monitoring and third-party legal defense forced by the regulator/customer reaction to a personal-data breach",
                "citations": [],
                "verified": False,
                "composition_role": "contributing",
                "share": 0.06,
            },
        ],
        [
            {
                "form": "response",
                "kind": "primary",
                "magnitude_basis": "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md)",
                "citations": [],
                "verified": False,
                "composition_role": "contributing",
                "share": 0.1,
            },
            {
                "form": "reputation",
                "kind": "secondary",
                "magnitude_basis": "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md)",
                "citations": [],
                "verified": False,
                "composition_role": "dominant",
                "share": 0.12,
            },
            {
                "form": "fines",
                "kind": "secondary",
                "magnitude_basis": "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md)",
                "citations": [],
                "verified": False,
                "composition_role": "contributing",
                "share": 0.1,
            },
            {
                "form": "response",
                "kind": "secondary",
                "magnitude_basis": "envelope-share (analyst-judged, vulnerability-grade; per loss-form-share-rubric.md §4 regulator-and-judgment reaction rule): notification, credit monitoring and third-party legal defense forced by the regulator/customer reaction to a personal-data breach",
                "citations": [],
                "verified": False,
                "composition_role": "contributing",
                "share": 0.08,
            },
        ],
    ),
    (
        "accidental-insider-exposure",
        "primary_loss",
        {
            "distribution": "PERT",
            "low": 4079.8104862974,
            "mode": 4079.8104862974,
            "high": 1095047.33489989,
        },
        {
            "distribution": "PERT",
            "low": 3399.842071894,
            "mode": 3399.842071894,
            "high": 912539.4457443901,
        },
    ),
    (
        "accidental-insider-exposure",
        "secondary_loss",
        {
            "distribution": "PERT",
            "low": 9519.5578014825,
            "mode": 9519.5578014825,
            "high": 2555110.4481324335,
        },
        {
            "distribution": "PERT",
            "low": 10199.5262160072,
            "mode": 10199.5262160072,
            "high": 2737618.337320474,
        },
    ),
    ("accidental-insider-exposure", "threat_event_type", "insider_misuse", "data_disclosure"),
    ("bec-fraud-financial", "asset_class", "data", "cash_or_equivalent"),
    ("chemical-process-safety-attack", "status", "published", "deprecated"),
    ("competitor-trade-secret-recruit", "asset_class", "people", "data"),
    (
        "competitor-trade-secret-recruit",
        "canonical_fair_gap",
        "FAIR's SOCIAL_ENGINEERING archetype addresses deception to gain system access. "
        "Competitor recruitment for trade-secret exfiltration is a structurally different "
        "threat: the asset at risk is people (the knowledge carrier), the attack vector is "
        "a job offer (not a phishing email), and the primary loss is litigation + "
        "competitive-advantage destruction rather than data-breach notification costs. The "
        "control failure is in offboarding processes and data-access governance, not in "
        "technical perimeter defenses. TEF is campaign-level: one competitor recruitment "
        "campaign targeting the employee population is one TEF event; per-employee-approach "
        "frequency is NOT the TEF unit. Attribution to competitors is prospective for any "
        "individual incident — this is the analyst-judged threat_actor_type for the "
        "archetype; individual disclosed cases (Waymo/Uber) may differ in actor structure. "
        "Attribution is prospective, not incident-derived in aggregate: most competitor "
        "IP-theft cases involve a corporate actor directing the recruitment, but public "
        "incidents also feature rogue employees acting without employer direction — "
        "analyst-judged as competitor-directed for this archetype's primary threat scenario.",
        "FAIR's SOCIAL_ENGINEERING archetype addresses deception to gain system access. "
        "Competitor recruitment for trade-secret exfiltration is a structurally different "
        "threat: the asset at risk is the trade secrets (data) that the recruited employee "
        "carries out, the attack vector is a job offer (not a phishing email), and the "
        "primary loss is litigation + competitive-advantage destruction rather than "
        "data-breach notification costs. The control failure is in offboarding processes "
        "and data-access governance, not in technical perimeter defenses. TEF is "
        "campaign-level: one competitor recruitment campaign targeting the employee "
        "population is one TEF event; per-employee-approach frequency is NOT the TEF unit. "
        "Attribution to competitors is prospective for any individual incident — this is "
        "the analyst-judged threat_actor_type for the archetype; individual disclosed cases "
        "(Waymo/Uber) may differ in actor structure. Attribution is prospective, not "
        "incident-derived in aggregate: most competitor IP-theft cases involve a corporate "
        "actor directing the recruitment, but public incidents also feature rogue employees "
        "acting without employer direction — analyst-judged as competitor-directed for this "
        "archetype's primary threat scenario.",
    ),
    (
        "credential-stuffing-consumer-portal",
        "applicable_industries",
        ["retail", "financial", "hospitality"],
        ["retail", "financial"],
    ),
    ("credential-stuffing-consumer-portal", "threat_event_type", "malware", "data_tampering"),
    ("data-breach-notification-regulatory-tail", "status", "published", "deprecated"),
    ("ddos-extortion-financial", "status", "published", "deprecated"),
    (
        "ddos-financial-seasonal-peak",
        "applicable_industries",
        ["financial", "retail"],
        ["financial"],
    ),
    (
        "ddos-financial-seasonal-peak",
        "applicable_org_sizes",
        ["large", "enterprise"],
        ["medium", "large", "enterprise"],
    ),
    (
        "ddos-financial-seasonal-peak",
        "description",
        "Volumetric DDoS attacks targeting payment processors, online banking portals, or "
        "retail checkout systems during peak transaction periods (Black Friday, tax season, "
        "quarter-end financial close). Attackers time attacks to maximize business pressure "
        "— a 1-hour outage of a payment processor during Black Friday has a loss that is "
        "orders of magnitude higher than the same outage in January. Mirai and MerisBot "
        "botnets have demonstrated multi-terabit attack capability. The financial loss is "
        "dominated by transaction revenue foregone during the outage.",
        "Volumetric DDoS attacks targeting payment processors and online banking portals "
        "during peak transaction periods (Black Friday, tax season, quarter-end financial "
        "close), sometimes accompanied by a ransom demand to stop the attack (extortion is "
        "a motive here, not a separate loss path). Attackers time attacks to maximize "
        "business pressure — a 1-hour outage of a payment processor during Black Friday has "
        "a loss that is orders of magnitude higher than the same outage in January. Mirai "
        "and MerisBot botnets have demonstrated multi-terabit attack capability. The "
        "financial loss is dominated by transaction revenue foregone during the outage.",
    ),
    ("education-student-records-insider", "asset_class", "people", "data"),
    (
        "energy-billing-system-tamper",
        "asset_class",
        "business_process_cost",
        "business_process_revenue",
    ),
    (
        "energy-billing-system-tamper",
        "canonical_fair_gap",
        "FAIR's DATA_TAMPERING archetype does not represent AMI/billing-system fraud, where "
        "the tampered asset is revenue-accounting data (meter reads and billing records) "
        "rather than operational or personal data, and the primary loss is unrecovered "
        "revenue (ongoing fraud drain) rather than a discrete breach event — requiring "
        "loss-period modeling distinct from the single-incident LM that FAIR's canonical "
        "form assumes.",
        "FAIR's DATA_TAMPERING archetype does not represent AMI/billing-system fraud, where "
        "the asset at risk is the billing (revenue) process and the tampered records are "
        "revenue-accounting data (meter reads and billing records) rather than operational "
        "or personal data, and the primary loss is unrecovered revenue (ongoing fraud "
        "drain) rather than a discrete breach event — requiring loss-period modeling "
        "distinct from the single-incident LM that FAIR's canonical form assumes.",
    ),
    (
        "energy-settlement-platform-tampering-offtaker-liability",
        "description",
        "A data-tampering or availability attack on the energy-trading or "
        "wholesale-settlement platform (ETRM, ISO/RTO scheduling interface, or bilateral "
        "PPA settlement system) corrupts or halts the settlement process for "
        "power-purchase agreement (PPA) or wholesale energy contracts. The offtaker or "
        "merchant counterparty loses contracted energy revenue because metered delivery "
        "cannot be confirmed, invoices are corrupted, or settlement windows expire without "
        "valid data. The generator/trader (the attacked org) faces make-whole liability or "
        "dispute-resolution costs owed to the counterparty whose settlement was corrupted "
        "— i.e. third-party revenue disruption, not the org's own generation revenue. "
        "Inherent posture assumes no immutable audit trail on ETRM settlement records, no "
        "dual-path settlement verification, and no offline backup of metering data.",
        "A state-sponsored actor (the Sandworm pattern in this entry's example incidents) "
        "mounts a data-tampering or availability attack on the energy-trading or "
        "wholesale-settlement platform (ETRM, ISO/RTO scheduling interface, or bilateral "
        "PPA settlement system) that corrupts or halts the settlement process for "
        "power-purchase agreement (PPA) or wholesale energy contracts. The offtaker or "
        "merchant counterparty loses contracted energy revenue because metered delivery "
        "cannot be confirmed, invoices are corrupted, or settlement windows expire without "
        "valid data. The generator/trader (the attacked org) faces make-whole liability or "
        "dispute-resolution costs owed to the counterparty whose settlement was corrupted "
        "— i.e. third-party revenue disruption, not the org's own generation revenue. "
        "Inherent posture assumes no immutable audit trail on ETRM settlement records, no "
        "dual-path settlement verification, and no offline backup of metering data.",
    ),
    (
        "energy-settlement-platform-tampering-offtaker-liability",
        "example_incidents",
        "Ukraine power-grid cyberattacks (2015/2016 Sandworm — SCADA and control-centre "
        "attacks disrupting settlement-relevant dispatch data; ICS-CERT Alert "
        "ICS-ALERT-14-281-01B). FERC and NERC CIP enforcement actions for settlement-system "
        "access-control failures (publicly reported patterns). FCA (UK) supervisory "
        "notices regarding energy-trading platform integrity incidents. Specific per-org "
        "settlement-liability figures are commercially sensitive and non-public.",
        "Ukraine power-grid cyberattacks (2015/2016 Sandworm — SCADA and control-centre "
        "attacks that de-energised substations; SANS ICS, 'Confirmation of a Coordinated "
        "Attack on the Ukrainian Power Grid', 2016-01; CISA ICS-CERT Alert "
        "ICS-ALERT-17-206-01, CRASHOVERRIDE Malware). These ground the actor and "
        "capability class behind the nation_state label; no public settlement-platform "
        "incident is attributable to it. FERC and NERC CIP enforcement actions for "
        "settlement-system access-control failures (publicly reported patterns). FCA (UK) "
        "supervisory notices regarding energy-trading platform integrity incidents. "
        "Specific per-org settlement-liability figures are commercially sensitive and "
        "non-public.",
    ),
    ("financial-call-center-social-eng", "asset_class", "people", "cash_or_equivalent"),
    ("financial-transaction-tampering", "threat_event_type", "data_tampering", "insider_misuse"),
    ("gov-citizen-portal-ddos", "asset_class", "business_process_revenue", "systems"),
    ("gov-employee-insider-leak", "asset_class", "people", "data"),
    ("gov-records-tampering", "threat_event_type", "data_tampering", "insider_misuse"),
    (
        "grid-protective-relay-manipulation",
        "description",
        "A nation-state actor manipulates electric-substation protection and control — "
        "issuing unauthorized breaker-open commands, abusing IEC 61850 / 60870-5-104 "
        "protocols, or disabling protective relays so faults are not cleared. The effect "
        "ranges from operator-visible load loss to equipment-damaging protection defeat, "
        "the signature capability demonstrated by the Industroyer/CRASHOVERRIDE and "
        "Industroyer2 toolsets against Ukrainian grid operators.",
        "A nation-state actor defeats electric-substation protection — disabling or "
        "mis-setting protective relays, or abusing IEC 61850 / 60870-5-104 protocols, so "
        "that faults are not cleared — causing equipment damage (transformers, lines, "
        "generators) and extended restoration. The signature capability was demonstrated "
        "by the Industroyer/CRASHOVERRIDE and Industroyer2 toolsets against Ukrainian grid "
        "operators. Operator-visible load loss from unauthorized breaker-open commands is "
        "modelled by denial-of-control, not here.",
    ),
    ("healthcare-record-alteration", "threat_event_type", "data_tampering", "insider_misuse"),
    ("healthcare-staff-credential-phish", "asset_class", "people", "data"),
    (
        "professional-payroll-bec",
        "description",
        "Cybercriminals impersonate executives or HR staff via email compromise to "
        "redirect employee direct-deposit accounts to attacker-controlled bank accounts, "
        "or to authorize fraudulent wire transfers to fictitious vendors. "
        "Professional-services firms (law, accounting, consulting, engineering) are "
        "high-value BEC targets because of their client trust relationships, high-value "
        "payment flows, and relatively small AP/HR teams with limited dual-control over "
        "fund transfers. Loss includes unrecoverable wire transfers, wire-recovery fees, "
        "payroll-system forensics, and regulatory/employment-law exposure for misdirected "
        "payroll.",
        "Cybercriminals impersonate executives or HR staff via email compromise to "
        "redirect employee direct-deposit accounts to attacker-controlled bank accounts. "
        "Professional-services firms (law, accounting, consulting, engineering) are "
        "high-value BEC targets because of their client trust relationships, high-value "
        "payment flows, and relatively small AP/HR teams with limited dual-control over "
        "fund transfers. Loss includes unrecoverable diverted payroll deposits, "
        "funds-recovery fees, payroll-system forensics, and regulatory/employment-law "
        "exposure for misdirected payroll.",
    ),
    ("retail-ecommerce-checkout-ddos", "applicable_industries", ["retail_trade"], ["retail"]),
    (
        "safety-system-bypass",
        "applicable_sub_sectors",
        ["oil_and_gas", "chemical_manufacturing", "nuclear", "pipeline"],
        ["oil_and_gas", "chemical_manufacturing", "nuclear", "pipeline", "process_manufacturing"],
    ),
    (
        "safety-system-bypass",
        "canonical_fair_gap",
        "FAIR has no archetype for safety-layer attacks where the primary loss is the "
        "defeat of physical safety barriers. Standard FAIR magnitude models (productivity "
        "loss, response cost, competitive advantage, legal/regulatory, reputation) do not "
        "capture the catastrophic-tail loss associated with loss of SIS protection: a "
        "major industrial accident (explosion, toxic release, fatalities) could result in "
        "losses exceeding the entire enterprise value. This archetype requires a bimodal "
        "magnitude distribution — near-zero if attack fails pre-consequence, catastrophic "
        "if process upset occurs unprotected.",
        "FAIR has no archetype for safety-layer attacks where the primary loss is the "
        "defeat of physical safety barriers. Standard FAIR magnitude models (productivity "
        "loss, response cost, competitive advantage, legal/regulatory, reputation) do not "
        "capture the catastrophic-tail loss associated with loss of SIS protection: a "
        "major industrial accident (explosion, toxic release, fatalities) could result in "
        "losses exceeding the entire enterprise value. This archetype requires a bimodal "
        "magnitude distribution — near-zero if attack fails pre-consequence, catastrophic "
        "if process upset occurs unprotected. Epic F (#192 campaign, 2026-09-29): this "
        "entry absorbed chemical-process-safety-attack (deprecated). Its TEF (PERT "
        "0.01/0.05/0.25, mean 0.0767) supersedes the #505 chemical TEF differentiation "
        "(PERT 0.005/0.03/0.15, mean 0.0458), which was an R15 anti-templating fix rather "
        "than evidence; a chemical or process-manufacturing organisation re-selecting this "
        "entry sees inherent ALE of about 1.6x the deprecated entry's.",
    ),
    (
        "safety-system-bypass",
        "description",
        "Adversary targets a Safety Instrumented System (SIS) or Emergency Shutdown Device "
        "(ESD) to disable or defeat safety functions, enabling unsafe process conditions to "
        "persist without triggering protective shutdowns. Triton/TRISIS is the canonical "
        "case: attackers reprogrammed Schneider Electric Triconex controllers to force a "
        "fail-safe state (causing unplanned shutdown) while attempting to mask the SIS from "
        "detecting abnormal process conditions. The intent was to enable a separate process "
        "upset to occur without SIS intervention, potentially causing a catastrophic "
        "explosion or toxic release.",
        "Adversary targets a Safety Instrumented System (SIS) or Emergency Shutdown Device "
        "(ESD) to disable or defeat safety functions, enabling unsafe process conditions to "
        "persist without triggering protective shutdowns. Triton/TRISIS is the canonical "
        "case: attackers reprogrammed Schneider Electric Triconex controllers to force a "
        "fail-safe state (causing unplanned shutdown) while attempting to mask the SIS from "
        "detecting abnormal process conditions. The intent was to enable a separate process "
        "upset to occur without SIS intervention, potentially causing a catastrophic "
        "explosion or toxic release. In chemical and petrochemical processes the "
        "unprotected upset takes the form of an overpressure, exotherm/runaway reaction or "
        "toxic release.",
    ),
    (
        "safety-system-bypass",
        "suggested_control_ids",
        [
            "network-segmentation",
            "file-integrity-monitoring",
            "user-access-control",
            "security-information-event-management",
            "change-management",
        ],
        [
            "network-segmentation",
            "file-integrity-monitoring",
            "user-access-control",
            "security-information-event-management",
            "change-management",
            "incident-response",
        ],
    ),
    ("telecom-sim-swap-fraud", "asset_class", "people", "cash_or_equivalent"),
    (
        "web-app-exploitation",
        "applicable_industries",
        ["financial", "retail", "information", "healthcare", "public"],
        ["financial", "retail", "information", "healthcare", "public", "hospitality"],
    ),
    (
        "web-app-exploitation",
        "description",
        "Cybercriminals exploit a vulnerability in an internet-facing web application or "
        "API — SQL injection, broken access control / IDOR, server-side request forgery, "
        "or an unauthenticated API endpoint — to read and exfiltrate the backing data "
        "store. Basic web-application attacks are the single largest pattern in "
        "external-breach data, and the loss is dominated by the volume and sensitivity of "
        "records exposed plus the downstream breach-notification and regulatory tail.",
        "Cybercriminals exploit a vulnerability in an internet-facing web application or "
        "API — SQL injection, broken access control / IDOR, server-side request forgery, "
        "or an unauthenticated API endpoint — to read and exfiltrate the backing data "
        "store. Basic web-application attacks are the single largest pattern in "
        "external-breach data, and the loss is dominated by the volume and sensitivity of "
        "records exposed plus the downstream breach-notification and regulatory tail. "
        "Where personal data is exposed, mandatory notification regimes shape that tail: "
        "GDPR (72-hour notice to the supervisory authority; fines up to 4% of global "
        "annual revenue), CCPA/CPRA (statutory damages of $100–$750 per consumer per "
        "incident), HIPAA and state breach laws, plus class-action exposure.",
    ),
    (
        "web-app-exploitation",
        "suggested_control_ids",
        [
            "web-application-firewall",
            "dynamic-application-security-testing",
            "static-application-security-testing",
            "secure-coding-practices",
            "penetration-testing",
        ],
        [
            "web-application-firewall",
            "dynamic-application-security-testing",
            "static-application-security-testing",
            "secure-coding-practices",
            "penetration-testing",
            "incident-response",
            "cyber-insurance",
        ],
    ),
)

# Note: SUPERSEDED (the set of (slug, column) cells a later migration legitimately
# re-changes) is a *test-side* bookkeeping concept -- see
# tests/migrations/test_epic_f_library_curation.py -- not a runtime constant this
# migration's upgrade()/downgrade() consults; the migration always applies its own
# frozen `old`/`new` literals regardless of what a later migration does to the same
# cell.

_TABLE = sa.table(
    "scenario_library_entries",
    *[sa.column(c) for c in sorted(_ALLOWED_COLUMNS)],
    sa.column("slug"),
    sa.column("version"),
    sa.column("source"),
)


def _validate_changes(changes: tuple[tuple[str, str, object, object], ...]) -> None:
    """Every column name in `changes` must be a vetted literal from
    _ALLOWED_COLUMNS -- an explicit raise, never an assert (assertions are
    stripped under `python -O`), so a typo'd or malicious column name can
    never reach SQL construction."""
    for slug, column, _old, _new in changes:
        if column not in _ALLOWED_COLUMNS:
            raise RuntimeError(
                f"epic-f library curation: column {column!r} (slug {slug!r}) is not in "
                "_ALLOWED_COLUMNS; refusing to build SQL for an unvetted column name"
            )


def _decode(col: str, raw: object) -> object:
    """DB value -> Python value for comparison. JSON columns: None for SQL NULL or the
    JSON literal 'null'; json.loads for a str; passed through unchanged otherwise (a
    backend that already deserializes JSON columns). Text/enum columns: passed through."""
    if col in _JSON_COLUMNS:
        if raw is None or raw == "null":
            return None
        if isinstance(raw, str):
            return json.loads(raw)
        return raw
    return raw


def _encode(col: str, value: object) -> object:
    """Python value -> DB bind value. JSON columns: None stays None (SQL NULL); anything
    else is json.dumps'd. Text/enum columns: passed through unchanged."""
    if col in _JSON_COLUMNS:
        return None if value is None else json.dumps(value)
    return value


def _classify(
    col: str, raw: object, old: object, new: object
) -> Literal["apply", "already", "drift"]:
    """Decode `raw` (the current DB value) and compare to `old`/`new`. `new` is checked
    first: on a fresh DB an inserting migration may already have written `new` from the
    live seed JSON, which must never be mistaken for `old` even if `old` happens to
    parsed-equal `new`'s repr in some degenerate case."""
    current = _decode(col, raw)
    if current == new:
        return "already"
    if current == old:
        return "apply"
    return "drift"


def _group_by_slug(
    changes: tuple[tuple[str, str, object, object], ...],
) -> dict[str, tuple[tuple[str, object, object], ...]]:
    """Groups `changes` by slug, preserving each cell's (column, old, new) and the
    slugs' first-appearance order (== alphabetical, since `_CHANGES` is sorted by
    (slug, column))."""
    groups: dict[str, list[tuple[str, object, object]]] = {}
    for slug, column, old, new in changes:
        groups.setdefault(slug, []).append((column, old, new))
    return {slug: tuple(cells) for slug, cells in groups.items()}


def _classify_slug_group(
    cells: tuple[tuple[str, object, object], ...],
    found_raw: dict[str, tuple[bool, object]],
) -> tuple[dict[str, Literal["apply", "already", "drift"]], tuple[str, ...]]:
    """Per-slug atomicity (spec §4.1 amendment, M4-1): classifies every cell of one
    slug's group first (`cells` as (column, target_from, target_to) -- already
    direction-adjusted by the caller), then enforces the group guard: if ANY cell is
    genuinely "drift" (found but neither `target_from` nor `target_to`) or its row is
    missing (`found_raw[column][0]` is False), every "apply"-eligible cell in the group
    is reclassified to "drift" too (an "already" cell is left as "already" -- it needs
    no write regardless of its siblings). Returns (final per-column classification,
    the columns that were genuinely drifted/missing -- the latter is what a caller logs
    in its one-per-slug warning, never the full reclassified set, so the message names
    only the true anomaly)."""
    raw_classification: dict[str, Literal["apply", "already", "drift"]] = {}
    for column, target_from, target_to in cells:
        found, raw = found_raw[column]
        raw_classification[column] = (
            "drift" if not found else _classify(column, raw, target_from, target_to)
        )
    drifted_columns = tuple(sorted(c for c, cls in raw_classification.items() if cls == "drift"))
    if not drifted_columns:
        return raw_classification, drifted_columns
    final: dict[str, Literal["apply", "already", "drift"]] = {
        column: ("drift" if cls == "apply" else cls) for column, cls in raw_classification.items()
    }
    return final, drifted_columns


def _run(reverse: bool) -> None:
    _validate_changes(_CHANGES)
    bind = op.get_bind()
    applied = already = drift = 0
    label = "downgrade" if reverse else "upgrade"
    for slug, cells in _group_by_slug(_CHANGES).items():
        dir_cells = tuple(
            (column, (new, old) if reverse else (old, new)) for column, old, new in cells
        )
        found_raw: dict[str, tuple[bool, object]] = {}
        for column, (_target_from, _target_to) in dir_cells:
            row = bind.execute(
                sa.select(_TABLE.c[column]).where(
                    _TABLE.c.slug == slug,
                    _TABLE.c.version == 1,
                    _TABLE.c.source == "seed",
                )
            ).first()
            found_raw[column] = (row is not None, row[0] if row is not None else None)

        classify_input = tuple(
            (column, target_from, target_to) for column, (target_from, target_to) in dir_cells
        )
        classification, drifted_columns = _classify_slug_group(classify_input, found_raw)

        if drifted_columns:
            logger.warning(
                "epic-f library curation %s: drift in slug=%s; skipping whole slug "
                "(atomic group) -- drifted column(s): %s",
                label,
                slug,
                ", ".join(drifted_columns),
            )

        for column, (_target_from, target_to) in dir_cells:
            cls = classification[column]
            if cls == "already":
                already += 1
            elif cls == "drift":
                drift += 1
            else:  # "apply"
                bind.execute(
                    sa.update(_TABLE)
                    .where(
                        _TABLE.c.slug == slug,
                        _TABLE.c.version == 1,
                        _TABLE.c.source == "seed",
                    )
                    .values(**{column: _encode(column, target_to)})
                )
                applied += 1
    # Forward (upgrade) summary line format is pinned by test (b)/(d):
    # "epic-f library curation: applied=<N> already_new=<M> drift=<K>" (no
    # direction suffix). Downgrade mirrors the wording with "already_old" since
    # "already" there means the row already holds `old`, not `new`. Counters are
    # CELL-level, not slug-level -- see the module docstring's "Counter semantics" note.
    if reverse:
        logger.info(
            "epic-f library curation downgrade: applied=%d already_old=%d drift=%d",
            applied,
            already,
            drift,
        )
    else:
        logger.info(
            "epic-f library curation: applied=%d already_new=%d drift=%d",
            applied,
            already,
            drift,
        )


def upgrade() -> None:
    _run(reverse=False)


def downgrade() -> None:
    _run(reverse=True)
