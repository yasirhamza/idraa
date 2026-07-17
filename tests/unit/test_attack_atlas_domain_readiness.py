"""#482: MITRE ATLAS third-domain schema readiness.

Pins the mechanical readiness surface (domains tuple, catalog schema Literals,
AML.T####/AML.TA#### id patterns with the parent-only rule held for ATLAS,
atlas.mitre.org urls, single-sourced labels/order) so the eventual ATLAS data
seeding is a pure data PR. Catalog CONTENT seeding is deliberately absent —
gated on AI-scenario growth per the issue.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from idraa.models.attack import ATTACK_DOMAINS, DOMAIN_LABELS, DOMAIN_ORDER
from idraa.schemas.attack_catalog import AttackTacticSeed, AttackTechniqueSeed


def test_atlas_in_domain_registry() -> None:
    assert ATTACK_DOMAINS == ("enterprise", "ics", "atlas")
    assert DOMAIN_LABELS["atlas"] == "ATLAS"
    assert list(DOMAIN_ORDER) == list(ATTACK_DOMAINS)  # display order == tuple order


def _technique(tid: str) -> AttackTechniqueSeed:
    return AttackTechniqueSeed(
        domain="atlas",
        technique_id=tid,
        name="LLM Prompt Injection",
        tactics=["AML.TA0000"],
        url="https://atlas.mitre.org/techniques/AML.T0051",
        citation={"source": "MITRE ATLAS", "url": "https://atlas.mitre.org/"},
    )


def test_catalog_accepts_atlas_parent_technique() -> None:
    seed = _technique("AML.T0051")
    assert seed.domain == "atlas"
    assert seed.technique_id == "AML.T0051"


@pytest.mark.parametrize("bad", ["AML.T0051.000", "T1059.001", "AMLT0051", "AML.T51", "X1234"])
def test_catalog_rejects_subtechniques_and_garbage(bad: str) -> None:
    # Parent-only convention held for ATLAS too (PR-1 rule).
    with pytest.raises(ValidationError):
        _technique(bad)


def test_catalog_accepts_atlas_tactic_and_url() -> None:
    seed = AttackTacticSeed(
        domain="atlas",
        tactic_id="AML.TA0000",
        shortname="ml-model-access",
        name="ML Model Access",
        display_order=0,
        url="https://atlas.mitre.org/tactics/AML.TA0000",
    )
    assert seed.tactic_id == "AML.TA0000"
    with pytest.raises(ValidationError):
        AttackTacticSeed(
            domain="atlas",
            tactic_id="AML.TA0000",
            shortname="x",
            name="X",
            display_order=0,
            url="https://example.org/evil",  # non-mitre url rejected at the seed gate
        )
