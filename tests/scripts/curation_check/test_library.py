"""Loader keeps every entry (data-contract policy); matching options stay unambiguous;
criteria examples never contradict the seed's own labels (M-I1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.curation_check.criteria import load_criteria
from scripts.curation_check.library import (
    CONTROL_FILE,
    SCENARIO_FILES,
    load_controls,
    load_scenarios,
    match_options,
    seed_hashes,
)

# Reviewed classification of every criteria example (M-I1, N-I2r2). An example either names a library
# control (anchor: that control must carry the example's sub-function) or names none. A new example
# must be added to one of the two, so drift is caught here, not in a campaign.
EXAMPLE_ANCHORS: dict[str, str | tuple[str, ...]] = {
    "24/7 SOC review of alerts": "managed-detection-response",
    "Acceptable-use policy": "acceptable-use-policy",
    "Allow only approved devices to join the network": "network-access-control",
    "Block access to known-malicious destinations": "secure-web-gateway",
    "Change-management approval board": "change-management",
    "Commercial threat-intelligence feeds": "threat-intelligence-program",
    "Configuration management database": "asset-management",
    "Configuration-drift detection": "security-configuration-assessment",
    "Cyber insurance policy": "cyber-insurance",
    "Data-handling standard": "data-classification-handling",
    "EDR telemetry collection": "endpoint-detection-response",
    "Encryption at rest and in transit": ("data-at-rest-encryption", "data-in-transit-encryption"),
    "Endpoint protection that blocks malicious code": "endpoint-detection-response",
    "Hot or warm disaster-recovery site": "business-continuity-disaster-recovery",
    "Isolate a compromised host": "incident-response",
    "Least-privilege access control": "user-access-control",
    "Multi-factor authentication": "multi-factor-authentication",
    "NetFlow or network tap capture": "network-detection-response",
    "Patch deployment process": "patch-management",
    "Patching known software vulnerabilities": "patch-management",
    "Penetration testing": "penetration-testing",
    "Quantitative (FAIR) risk analysis": "cyber-risk-quantification-management",
    "Revoke session tokens and disable a compromised account": "incident-response",
    "SIEM correlation pipeline": "security-information-event-management",
    "Security awareness communications": "security-awareness-training",
    "Software bill of materials": "software-bill-of-materials",
    "Tested business continuity plan": "business-continuity-disaster-recovery",
    "Tested restore from backups": "data-backup-recovery",
    "Vulnerability scanning": "vulnerability-assessment",
}

NO_LIBRARY_CONTROL = frozenset(
    {
        "Adequate security budget and staffing",
        "Analyst triage playbooks",
        "Attack-path analysis",
        "GRC control-status register",
        "Automated alerting on security events",
        "Block a malicious source IP",
        "Board risk briefings",
        "Bonus tied to security KPIs",
        "Breach-notification procedure and templates",
        "Business impact analysis",
        "Change blackout windows",
        "Code of conduct",
        "Code-freeze periods",
        "Configuration remediation push",
        "Pre-deployment testing of changes in a staging environment",
        "Control catalogue with effectiveness ratings",
        "Control-coverage maps",
        "Data catalogue with sensitivity labels",
        "Decision-support tools for managers",
        "Decommission an unused internet-facing service",
        "Designated security leadership with authority",
        # a monitoring activity over agent coverage, not the Asset Management control itself
        "Asset-inventory reconciliation of endpoint-agent coverage",
        "Exception and waiver tracking",
        "Exception process with sign-off",
        "Executive risk dashboards",
        "Expert on-call rotation",
        "Governance committee review cycles",
        "Hotfix release process",
        "ISAC participation",
        "Internal incident history",
        "Just-in-time policy reminders",
        "Kill a malicious process",
        "Legal hold and claims handling",
        "Legal-action and monitoring notices at login",
        "MITRE ATT&CK mapping of relevant techniques",
        "Monthly risk reports",
        "Multi-region failover",
        "Operating system and application logging",
        "Control-effectiveness scorecard for management",
        "Penalties for policy violations",
        "Performance reviews weighting risk-aware behaviour",
        "Periodic control testing",
        "Periodic review of management decisions against the risk appetite",
        "Policy acknowledgement at onboarding",
        "Policy-change announcements",
        "Publicised prosecution of offenders",
        "Recognition for good security choices",
        "Redundant service providers",
        "Risk assessment and risk register",
        "Risk-appetite and tolerance statement",
        "SaaS audit-log export",
        "Scenario modelling",
        "Scheduled daily log review",
        "Sector-specific threat reports",
        "Severity-based remediation playbooks",
        "Signature and heuristic malware databases",
        "Stop collecting unneeded sensitive data",
        "System criticality ratings",
        "Threat-actor profiles",
        "Threat-hunting hypotheses",
        "Tuned alert thresholds and detection rules",
        "Vendor security bulletin tracking",
        "Vendor-selection security criteria",
        "Visible CCTV with warning signs",
        "Emergency configuration rollback to restore a degraded control",
        "Automated regression tests gating each release",
        "Release acceptance checks against security requirements",
        "Fix, mitigate or accept decision for each finding",
    }
)

# Examples where Idraa's enum text and the seed disagree (docs/reference/scenario-labelling-conventions.md).
CONTESTED_EXAMPLES = frozenset(
    {
        "Network segmentation",
        "Restore-from-backup procedure",
        "Incident response retainer",
        "Pre-merge SAST and dependency scanning",
        "Periodic threat-modelling workshops",
        "Internal audit of policy compliance",
        "Policy-compliance scans",
        "Role-based security training",
        "Audit and assessment results",
        "Pen-test findings summaries",
        "Mandatory peer code review",
        "Secure build baselines applied at deployment",
        "Standardised, locked-down configurations",
        "Risk-based patch prioritisation",
        "Vulnerability-management triage workflow",
    }
)

# Examples whose natural control deliberately dropped the claim in the seed's _meta.claim_drops (NICE-4).
DROPPED_ANCHORS = {
    "Penalties for policy violations": ("acceptable-use-policy", "dsc_prev_incentives"),
    "Legal-action and monitoring notices at login": (
        "acceptable-use-policy",
        "lec_prev_deterrence",
    ),
}


def _scenario(slug: str, name: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": name,
        "status": "published",
        "description": f"{name} description",
        "threat_event_type": "ransomware",
        "asset_class": "data",
        "threat_actor_type": "cybercriminals",
    }


def _write_seed(
    root: Path,
    scenarios: list[dict[str, Any]],
    extension: list[dict[str, Any]],
    controls: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
) -> None:
    (root / "data").mkdir(parents=True)
    (root / SCENARIO_FILES[0]).write_text(json.dumps(scenarios))
    (root / SCENARIO_FILES[1]).write_text(json.dumps(extension))
    (root / CONTROL_FILE).write_text(json.dumps({"_meta": meta or {}, "entries": controls}))


def test_load_scenarios_keeps_every_entry_across_both_files(tmp_path: Path) -> None:
    _write_seed(tmp_path, [_scenario("a", "A"), _scenario("b", "B")], [_scenario("c", "C")], [])
    items = load_scenarios(tmp_path)
    assert [i.slug for i in items] == ["a", "b", "c"]
    assert items[0].state() == {"scenario_name": "A", "scenario_description": "A description"}


def test_load_controls_keeps_every_function_and_dropped_claims(tmp_path: Path) -> None:
    control = {
        "slug": "siem",
        "name": "SIEM",
        "status": "published",
        "description": "Collects logs.",
        "assignments": [
            {"sub_function": "lec_det_monitoring"},
            {"sub_function": "lec_det_recognition"},
            {"sub_function": "vmc_id_control_monitoring"},
        ],
    }
    meta = {
        "claim_drops": [
            {"slug": "siem", "dropped": ["dsc_prev_incentives"], "reason": "not groundable"}
        ]
    }
    _write_seed(
        tmp_path, [], [], [control, {**control, "slug": "b"}, {**control, "slug": "c"}], meta
    )
    items = load_controls(tmp_path)
    assert [i.slug for i in items] == ["siem", "b", "c"]
    assert items[0].functions == frozenset(
        {"lec_det_monitoring", "lec_det_recognition", "vmc_id_control_monitoring"}
    )
    assert items[0].dropped == {"dsc_prev_incentives": "not groundable"}
    assert items[1].dropped == {}
    assert items[0].state() == {"control_name": "SIEM", "control_description": "Collects logs."}


def test_duplicate_claim_drop_is_rejected(tmp_path: Path) -> None:  # NICE-7
    control = {
        "slug": "siem",
        "name": "SIEM",
        "status": "published",
        "description": "d",
        "assignments": [],
    }
    meta = {
        "claim_drops": [
            {"slug": "siem", "dropped": ["dsc_prev_incentives"], "reason": "first"},
            {"slug": "siem", "dropped": ["dsc_prev_incentives"], "reason": "second"},
        ]
    }
    _write_seed(tmp_path, [], [], [control], meta)
    with pytest.raises(ValueError, match=r"siem.*dsc_prev_incentives"):
        load_controls(tmp_path)


def test_real_seed_loads_completely() -> None:
    from scripts.curation_check.config import REPO_ROOT

    raw_scenarios = sum(len(json.loads((REPO_ROOT / f).read_text())) for f in SCENARIO_FILES)
    raw = json.loads((REPO_ROOT / CONTROL_FILE).read_text())
    assert len(load_scenarios()) == raw_scenarios
    assert len(load_controls()) == len(raw["entries"])
    assert sum(len(c.dropped) for c in load_controls()) == sum(
        len(d["dropped"]) for d in raw["_meta"]["claim_drops"]
    )


def test_match_options_excludes_self_and_maps_names(tmp_path: Path) -> None:
    _write_seed(tmp_path, [_scenario("a", "A"), _scenario("b", "B"), _scenario("c", "C")], [], [])
    options, name_to_slug = match_options(load_scenarios(tmp_path), exclude_slug="b")
    assert options == {"A": "A description", "C": "C description"}
    assert name_to_slug == {"A": "a", "C": "c"}


def test_match_options_rejects_duplicate_names(tmp_path: Path) -> None:
    _write_seed(tmp_path, [_scenario("a", "Same"), _scenario("b", "Same")], [], [])
    with pytest.raises(ValueError, match=r"'a'.*'b'|'b'.*'a'"):
        match_options(load_scenarios(tmp_path))


def test_seed_hashes_cover_every_seed_file(tmp_path: Path) -> None:
    _write_seed(tmp_path, [], [], [])
    hashes = seed_hashes(tmp_path)
    assert set(hashes) == {str(f) for f in (*SCENARIO_FILES, CONTROL_FILE)}
    assert all(len(h) == 64 for h in hashes.values())


def test_every_example_is_classified_and_anchors_agree_with_seed_labels() -> None:  # M-I1, N-I2r2
    c = load_criteria()
    functions = {ctl.slug: ctl.functions for ctl in load_controls()}
    by_example = {e: slug for slug, s in c.subfunctions.items() for e in s["examples"]}
    assert set(EXAMPLE_ANCHORS).isdisjoint(NO_LIBRARY_CONTROL)
    assert set(by_example) == set(EXAMPLE_ANCHORS) | NO_LIBRARY_CONTROL
    conflicts = [
        (e, by_example[e], ctl)
        for e, anchor in EXAMPLE_ANCHORS.items()
        for ctl in ((anchor,) if isinstance(anchor, str) else anchor)
        if by_example[e] not in functions[ctl]
    ]
    assert conflicts == []


def test_dropped_anchors_match_the_seed_drops() -> None:  # NICE-4
    c = load_criteria()
    by_example = {e: slug for slug, s in c.subfunctions.items() for e in s["examples"]}
    controls = {ctl.slug: ctl for ctl in load_controls()}
    assert set(DROPPED_ANCHORS) <= NO_LIBRARY_CONTROL
    for example, (slug, fn) in DROPPED_ANCHORS.items():
        assert by_example[example] == fn, example
        assert fn in controls[slug].dropped, example
        assert fn not in controls[slug].functions, example


def test_contested_examples_are_absent() -> None:  # M-I1, owner decision 2026-09-28
    c = load_criteria()
    examples = {e for s in c.subfunctions.values() for e in s["examples"]}
    assert examples.isdisjoint(CONTESTED_EXAMPLES)
