from __future__ import annotations

import datetime as dt
import uuid

import pytest

from idraa.errors import ValidationError
from idraa.models.threat_community import ThreatCommunity, canonical_threat_community_id
from idraa.services.threat_communities import (
    ENUM_TO_COMMUNITY_SLUG,
    ThreatCommunityService,
    legacy_slug_for,
)

_ORG = uuid.uuid4()
_COPY_COLS = (
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


def org_row_from(tpl: ThreatCommunity, slug: str = "org_only") -> ThreatCommunity:
    """A source='org' row cloned from a seed row (shared by the confirm-route test)."""
    return ThreatCommunity(
        id=uuid.uuid4(),
        version=1,
        slug=slug,
        source="org",
        published_at=dt.datetime.now(dt.UTC),
        **{c: getattr(tpl, c) for c in _COPY_COLS},
    )


async def test_list_published_nine_internal_first(db_session, seed_threat_communities) -> None:
    rows = await ThreatCommunityService(db_session, organization_id=_ORG).list_published()
    assert len(rows) == 9
    assert [r.origin for r in rows] == sorted(
        (r.origin for r in rows), key=lambda o: 0 if o == "internal" else 1
    )


async def test_resolve_slug_and_unknown(db_session, seed_threat_communities) -> None:
    svc = ThreatCommunityService(db_session, organization_id=_ORG)
    assert await svc.resolve_slug("nation_state") == (
        canonical_threat_community_id("nation_state"),
        1,
    )
    with pytest.raises(ValidationError):
        await svc.resolve_slug("martians")


async def test_org_sourced_row_is_excluded(db_session, seed_threat_communities) -> None:
    """Review Focus #4 (service half): a non-seed row never resolves in P1."""
    db_session.add(org_row_from(seed_threat_communities["hacktivists"]))
    await db_session.flush()
    svc = ThreatCommunityService(db_session, organization_id=_ORG)
    assert len(await svc.list_published()) == 9
    assert await svc.get_by_slug("org_only") is None
    with pytest.raises(ValidationError):
        await svc.resolve_slug("org_only")


async def test_grouped_choices_shape(db_session, seed_threat_communities) -> None:
    groups = await ThreatCommunityService(db_session, organization_id=_ORG).grouped_choices()
    assert [g for g, _ in groups] == ["Internal", "External"] and sum(
        len(o) for _, o in groups
    ) == 9


def test_legacy_slug_for_rules() -> None:
    assert legacy_slug_for(None) == (None, "unassigned")
    assert legacy_slug_for("") == (None, "unassigned")
    assert legacy_slug_for("insider_malicious") == ("privileged_insider", "migrated_split_default")
    assert legacy_slug_for("hacktivists") == ("hacktivists", "migrated")
    with pytest.raises(KeyError):
        legacy_slug_for("martians")


def test_enum_to_community_slug_is_exhaustive_and_excludes_split_targets() -> None:
    """M3-N2: the six legacy ThreatActorType literals (pre-P1 enum) map 1:1 here --
    no more, no fewer -- and none of the three P1-only remap targets (which never
    existed as a legacy enum value) can appear on the right-hand side."""
    assert set(ENUM_TO_COMMUNITY_SLUG) == {
        "cybercriminals",
        "nation_state",
        "hacktivists",
        "competitors",
        "insider_accidental",
        "insider_malicious",
    }
    assert set(ENUM_TO_COMMUNITY_SLUG.values()).isdisjoint(
        {"third_party", "opportunistic_hackers", "nonprivileged_insider"}
    )
