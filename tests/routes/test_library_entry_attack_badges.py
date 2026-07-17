"""Route tests for provenance-badged ATT&CK technique chips on the library
entry-detail page (issue #475 P2, Task 6).

The card renders one chip per ``ScenarioLibraryEntryAttackMapping`` row scoped
to ``(entry.id, entry.version)``. Cited claims carry a visible checkmark
marker; expert-estimate claims render with a tooltip only — never dressed up
to look more certain than their provenance label.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from idraa.models.attack import AttackTechnique, ScenarioLibraryEntryAttackMapping
from idraa.models.enums import AssetClass, ThreatActorType, ThreatCategory
from idraa.models.scenario_library import ScenarioLibraryEntry


@pytest_asyncio.fixture
async def seed_library_entry_with_attack_mappings(
    db_session: AsyncSession,
) -> ScenarioLibraryEntry:
    """A published entry with one cited (2 citations) and one expert-estimate
    curated technique mapping."""
    entry = ScenarioLibraryEntry(
        id=_uuid.uuid4(),
        version=1,
        slug="phishing-credential-theft",
        name="Phishing Credential Theft",
        status="published",
        threat_event_type=ThreatCategory.SOCIAL_ENGINEERING,
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        asset_class=AssetClass.SYSTEMS,
        tags=[],
        description="d" * 25,
        canonical_fair_gap="g" * 25,
        source_citations=[],
        threat_event_frequency={"distribution": "PERT", "low": 1.0, "mode": 4.0, "high": 12.0},
        vulnerability={"distribution": "PERT", "low": 0.05, "mode": 0.20, "high": 0.50},
        primary_loss={
            "distribution": "PERT",
            "low": 100_000.0,
            "mode": 750_000.0,
            "high": 5_000_000.0,
        },
    )
    cited_technique = AttackTechnique(
        id=_uuid.uuid4(),
        domain="enterprise",
        technique_id="T1566",
        name="Phishing",
        description="Adversaries may send phishing messages.",
        tactics=["initial-access"],
        parent_technique_id=None,
        deprecated=False,
        catalog_version="18.0",
        url="https://attack.mitre.org/techniques/T1566/",
        citation={
            "source": "MITRE ATT&CK",
            "copyright": "© 2026 The MITRE Corporation.",
            "license": "MITRE ATT&CK Terms of Use",
            "document": "ATT&CK Enterprise Matrix",
            "attack_version": "18.0",
            "accessed": "2026-07-04",
        },
    )
    estimate_technique = AttackTechnique(
        id=_uuid.uuid4(),
        domain="enterprise",
        technique_id="T1204",
        name="User Execution",
        description="Adversaries may rely on user action.",
        tactics=["execution"],
        parent_technique_id=None,
        deprecated=False,
        catalog_version="18.0",
        url="https://attack.mitre.org/techniques/T1204/",
        citation={
            "source": "MITRE ATT&CK",
            "copyright": "© 2026 The MITRE Corporation.",
            "license": "MITRE ATT&CK Terms of Use",
            "document": "ATT&CK Enterprise Matrix",
            "attack_version": "18.0",
            "accessed": "2026-07-04",
        },
    )
    db_session.add_all([entry, cited_technique, estimate_technique])
    await db_session.flush()
    db_session.add_all(
        [
            ScenarioLibraryEntryAttackMapping(
                id=_uuid.uuid4(),
                library_entry_id=entry.id,
                library_entry_version=entry.version,
                technique_id=cited_technique.id,
                rationale="Phishing is the documented initial-access vector here.",
                provenance="cited",
                citations=["https://example.org/report-a", "https://example.org/report-b"],
            ),
            ScenarioLibraryEntryAttackMapping(
                id=_uuid.uuid4(),
                library_entry_id=entry.id,
                library_entry_version=entry.version,
                technique_id=estimate_technique.id,
                rationale="Analyst judgment: users commonly execute the payload.",
                provenance="expert-estimate",
                citations=[],
            ),
        ]
    )
    await db_session.commit()
    await db_session.refresh(entry)
    return entry


@pytest.mark.asyncio
async def test_detail_shows_attack_technique_chips(
    viewer_client, seed_library_entry_with_attack_mappings
):
    entry = seed_library_entry_with_attack_mappings
    r = await viewer_client.get(f"/library/entries/{entry.id}")
    assert r.status_code == 200
    body = r.text
    assert "ATT&amp;CK techniques" in body
    assert "T1566" in body
    assert "Phishing" in body
    assert "T1204" in body
    assert "User Execution" in body


@pytest.mark.asyncio
async def test_cited_chip_carries_provenance_tooltip_and_marker(
    viewer_client, seed_library_entry_with_attack_mappings
):
    entry = seed_library_entry_with_attack_mappings
    r = await viewer_client.get(f"/library/entries/{entry.id}")
    body = r.text
    assert "Cited &mdash; 2 primary citation(s)" in body
    assert "cited</span>" in body


@pytest.mark.asyncio
async def test_estimate_chip_carries_tooltip_and_no_cited_marker(
    viewer_client, seed_library_entry_with_attack_mappings
):
    entry = seed_library_entry_with_attack_mappings
    r = await viewer_client.get(f"/library/entries/{entry.id}")
    body = r.text
    assert 'title="Expert estimate"' in body
    # The estimate chip's own <span> block must not carry the cited marker.
    # Isolate the User Execution chip fragment and assert no "cited" marker
    # appears within it.
    idx = body.index("User Execution")
    fragment = body[max(0, idx - 400) : idx + 200]
    assert "&#10003; cited" not in fragment
