"""Armed guards for ATT&CK mapping curation provenance (issue #475).

Mirrors tests/contracts/test_library_provenance.py: a 'cited' mapping claim
MUST carry at least one non-whitespace citation; the ATT&CK technique page is
catalog attribution, NOT grounding for a mapping claim (spec §Curation
methodology).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from idraa.schemas.attack_catalog import (
    AttackTacticSeed,
    AttackTechniqueSeed,
    EntryAttackMappingSeed,
)


def _mapping_kw(**overrides):
    base = {
        "entry_slug": "ransomware-on-ehr",
        "domain": "enterprise",
        "technique_id": "T1486",
        "rationale": "Encryption-for-impact is the entry's core loss event.",
        "provenance": "expert-estimate",
        "citations": [],
    }
    base.update(overrides)
    return base


def test_cited_mapping_requires_citation():
    with pytest.raises(ValidationError):
        EntryAttackMappingSeed(**_mapping_kw(provenance="cited", citations=[]))


def test_cited_mapping_rejects_whitespace_citation():
    with pytest.raises(ValidationError):
        EntryAttackMappingSeed(**_mapping_kw(provenance="cited", citations=["   "]))


def test_expert_estimate_requires_rationale():
    with pytest.raises(ValidationError):
        EntryAttackMappingSeed(**_mapping_kw(rationale="  "))


def test_expert_estimate_rationale_must_not_claim_cited():
    """Meth-B1(c): an expert-estimate rationale asserting cited status is a
    provenance-label lie the syntactic citation guard can't catch."""
    with pytest.raises(ValidationError):
        EntryAttackMappingSeed(
            **_mapping_kw(rationale="Phishing is the cited pattern for this sector.")
        )
    # Substrings of other words don't trip the guard.
    EntryAttackMappingSeed(**_mapping_kw(rationale="Analysts are excited to note this."))


def test_rationale_length_capped():
    with pytest.raises(ValidationError):
        EntryAttackMappingSeed(**_mapping_kw(rationale="x" * 2001))


def test_valid_cited_mapping_passes():
    m = EntryAttackMappingSeed(
        **_mapping_kw(
            provenance="cited",
            citations=[
                "HHS HC3 Sector Alert 202304, p. 2 (accessed 2026-06-10)",
            ],
        )
    )
    assert m.provenance == "cited"


def test_unknown_provenance_rejected():
    with pytest.raises(ValidationError):
        EntryAttackMappingSeed(**_mapping_kw(provenance="vibes"))


def test_technique_id_format():
    with pytest.raises(ValidationError):
        AttackTechniqueSeed(
            domain="enterprise",
            technique_id="T1566.001",  # sub-technique format — rejected in PR 1
            name="x",
            tactics=["initial-access"],
            url="https://attack.mitre.org/techniques/T1566/001/",
            citation={"source": "MITRE ATT&CK"},
        )


def test_technique_requires_at_least_one_tactic():
    with pytest.raises(ValidationError):
        AttackTechniqueSeed(
            domain="ics",
            technique_id="T0813",
            name="Denial of Control",
            tactics=[],
            url="https://attack.mitre.org/techniques/T0813/",
            citation={"source": "MITRE ATT&CK"},
        )


def test_tactic_seed_extra_forbidden():
    with pytest.raises(ValidationError):
        AttackTacticSeed(
            domain="enterprise",
            tactic_id="TA0001",
            shortname="initial-access",
            name="Initial Access",
            display_order=0,
            url="https://attack.mitre.org/tactics/TA0001/",
            bogus_field=1,
        )
