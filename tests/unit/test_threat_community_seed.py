"""Pinning tests for data/seed_threat_communities.json (spec §4.1, §6)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from idraa.models.threat_community import CANONICAL_THREAT_COMMUNITY_SLUGS
from idraa.schemas.threat_community import (
    SEED_PATH,
    ThreatCommunitySeed,
    load_threat_community_seed,
)

_TEXT_FIELDS = (
    "name",
    "summary",
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
    "rationale",
)

# v1 content hashes — the canonical layer is immutable in code. Editing a v1 profile in
# place fails here; re-curation adds (id, version+1) in a SEPARATE seed file. Hash the RAW
# JSON row (not model_dump) so a later optional schema field does not change every hash.
# Fill once Task 1's content is final.
V1_CONTENT_SHA256: dict[str, str] = {
    "nation_state": "7cb6fdb579134c262ddbca0c79ea6fdda43957ee7abe2fa6adedb65a6ff112d4",
    "cybercriminals": "60f529af45635974f2dfa44c00060aea7d8de8d6d04f80d2ce3cc69577f825e4",
    "hacktivists": "e06a986717bb3a6c546b4d99bd36324c839032e531ba04de3d7695c843ddf53d",
    "competitors": "e0d71f2047ff4f56401955d43097ec7bd7eb0f250f2a7f0f58e4f98f1fe04d0e",
    "privileged_insider": "bdd10566ff2af4737fb86a60d44c5a4bc65a89e894ed56d7effe97a5a96e374f",
    "nonprivileged_insider": "87e486b0620241aab29127515de43d6d687aa3be32b9ebe99fd15a4141fd20ce",
    "insider_accidental": "590ff2e9d89be27d1919760ffb50c0b37338e50d43ef2a3c1e7fa257dfb7a923",
    "third_party": "e37085d0efc9f315c67e7fd518a1d5d0606219fc3af9510648acff37bab5a873",
    "opportunistic_hackers": "9a224bdb2af3d1ae292773c8cb4f13990cbd085789b7e2d947c70971ccadcd93",
}


def _raw_rows() -> list[dict]:
    return json.loads(Path(SEED_PATH).read_text(encoding="utf-8"))


def _row_hash(raw: dict) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def test_seed_has_exactly_the_nine_canonical_slugs() -> None:
    assert [r.slug for r in load_threat_community_seed()] == list(CANONICAL_THREAT_COMMUNITY_SLUGS)


def test_v1_content_is_pinned() -> None:
    assert set(V1_CONTENT_SHA256) == set(CANONICAL_THREAT_COMMUNITY_SLUGS), "pin every slug"
    for raw in _raw_rows():
        assert _row_hash(raw) == V1_CONTENT_SHA256[raw["slug"]], (
            f"{raw['slug']}: v1 edited in place — add a v2 instead"
        )


def test_every_text_attribute_is_non_blank() -> None:
    for r in load_threat_community_seed():
        for f in _TEXT_FIELDS:
            assert getattr(r, f).strip(), f"{r.slug}.{f} blank"


def test_landmarks_ordered_bounded_scaled_and_tcap_null_iff_non_malicious() -> None:
    for r in load_threat_community_seed():
        t = r.tef_landmark
        assert 0 < t.low <= t.mode <= t.high and t.low < t.high and t.derivation.strip(), r.slug
        assert (r.tcap_landmark is None) == (r.intent == "non_malicious"), r.slug
        if r.tcap_landmark is not None:
            c = r.tcap_landmark
            assert 0 <= c.low <= c.mode <= c.high <= 100 and c.low < c.high and c.high > 1, r.slug
            assert c.basis_class in ("cited", "convention") and c.derivation.strip()
        for tri in (t, r.tcap_landmark):
            if tri is not None and tri.basis_class == "cited":
                assert any(c.locator for c in r.citations), (
                    f"{r.slug}: cited landmark needs a citation locator"
                )


def test_citations_https_and_present() -> None:
    for r in load_threat_community_seed():
        assert r.citations and all(c.url.startswith("https://") for c in r.citations), r.slug


def test_origin_intent_partition() -> None:
    by = {r.slug: r for r in load_threat_community_seed()}
    assert {s for s, r in by.items() if r.origin == "internal"} == {
        "privileged_insider",
        "nonprivileged_insider",
        "insider_accidental",
        "third_party",
    }
    assert {s for s, r in by.items() if r.intent == "non_malicious"} == {"insider_accidental"}


def test_schema_round_trips_and_rejects_bad_input() -> None:
    raw_rows = json.loads(Path(SEED_PATH).read_text(encoding="utf-8"))
    for raw in raw_rows:
        seed = ThreatCommunitySeed.model_validate(raw)
        assert ThreatCommunitySeed.model_validate(seed.model_dump(mode="json")) == seed
    raw = next(r for r in raw_rows if r["tcap_landmark"] is not None)
    for bad in (
        {**raw, "bogus": 1},
        {**raw, "tef_landmark": {**raw["tef_landmark"], "low": 2.0, "mode": 1.0, "high": 3.0}},
        {**raw, "tcap_landmark": {**raw["tcap_landmark"], "low": 0.5, "mode": 0.6, "high": 0.9}},
        {**raw, "tcap_landmark": {**raw["tcap_landmark"], "basis_class": "derived"}},
        {**raw, "tcap_landmark": None},  # malicious with no tcap
        {**raw, "citations": [{"title": "x", "url": "javascript:alert(1)"}]},
    ):
        with pytest.raises(ValidationError):
            ThreatCommunitySeed.model_validate(bad)


def test_privileged_insider_states_control_bypass_and_every_definition_is_control_naive() -> None:
    by = {r.slug: r for r in load_threat_community_seed()}
    p = (by["privileged_insider"].capability + " " + by["privileged_insider"].rationale).lower()
    assert "preventative" in p or "preventive" in p
    for r in by.values():
        d = (
            r.threat_event_definition.lower()
        )  # the definition alone — it is what the wizard tooltip shows
        assert (
            "nothing intervening"
            if r.intent == "non_malicious"
            else "whether or not your controls stop it"
        ) in d, r.slug
        t = r.tef_landmark
        d_low = t.derivation.lower()
        # tripwires on EVERY row (the PR-gate methodology review is the semantic check):
        # the lead-in names the published statistic (a bare "mode" anywhere would be satisfied by
        # the mode-solve clause every derivation carries); the derivation states its placement;
        # a derived row is always mean-placed -- an attempt-level cited survey rate must not be
        # written straight into `mode` (M10-I1 / M11-I1), and a published mode must not be
        # relabelled as a mean to pass (M12-I1).
        lead = re.search(r"source statistic \(([^)]*)\)", d_low)
        assert lead and re.search(r"\b(mean|median|mode|probability)\b", lead.group(1)), (
            r.slug
        )  # the statistic is named IN the lead-in
        mean_placed, mode_placed = "placed as the pert mean" in d_low, "placed as the mode" in d_low
        assert mean_placed != mode_placed, f"{r.slug}: state exactly one placement"
        if "probability" in lead.group(1):
            assert re.search(r"ln\s*\(", t.derivation) and mean_placed, (
                f"{r.slug}: a probability source converts p->lambda and is mean-placed"
            )
        if t.basis_class == "derived":
            assert mean_placed, f"{r.slug}: a derived landmark is a mean"
        if t.basis_class != "convention":
            assert re.search(r"\bk\s*=", d_low), r.slug  # the spread factor is stated
        if t.basis_class in ("cited", "derived"):
            assert "share" in d_low, r.slug  # attributed to this community at any event level
            assert any(c.locator for c in r.citations), (
                f"{r.slug}: a {t.basis_class} landmark needs a located citation"
            )
        if "asymmetric" not in d_low:
            # symmetric x/÷ k spread: the stated central value is the geometric centre of the bounds
            gm = math.sqrt(t.low * t.high)
            centre = (t.low + 4 * t.mode + t.high) / 6 if mean_placed else t.mode
            assert abs(centre - gm) <= 1e-3 * gm, (
                f"{r.slug}: stated placement does not match the triple"
            )
        if t.source_event_level == "attempt":
            assert t.assumed_conversion is None, r.slug
        else:
            assert (
                t.assumed_conversion is not None
                and 0 < t.assumed_conversion <= 1
                and t.basis_class == "derived"
            ), r.slug
            # EITHER the p->lambda step (an annual-probability source, IRIS-class) OR an explicit
            # statement that the source statistic is already a PER-ORGANISATION rate (a corpus
            # total such as a DBIR incident count is not one: no exposure base).
            assert (
                re.search(r"ln\s*\(", t.derivation) or "already a per-organisation rate" in d_low
            ), r.slug
        if r.tcap_landmark is not None:
            assert not hasattr(r.tcap_landmark, "source_event_level")


def test_schema_rejects_missing_clause_missing_level_unconverted_source_and_tcap_with_level() -> (
    None
):
    raw_rows = json.loads(Path(SEED_PATH).read_text(encoding="utf-8"))
    raw = next(r for r in raw_rows if r["intent"] == "malicious" and r["tcap_landmark"] is not None)
    base_tef = {k: v for k, v in raw["tef_landmark"].items() if k != "assumed_conversion"}
    for bad in (
        {**raw, "threat_event_definition": "An attempt to break in."},  # no canonical clause
        {
            **raw,
            "tef_landmark": {
                k: v for k, v in raw["tef_landmark"].items() if k != "source_event_level"
            },
        },  # level missing
        {
            **raw,
            "tef_landmark": {**base_tef, "source_event_level": "incident"},
        },  # incident without assumed_conversion
        {
            **raw,
            "tef_landmark": {
                **base_tef,
                "source_event_level": "incident",
                "assumed_conversion": 0.3,
                "basis_class": "cited",
            },
        },  # must be derived
        {
            **raw,
            "tef_landmark": {
                **base_tef,
                "source_event_level": "attempt",
                "assumed_conversion": 0.3,
            },
        },  # attempt with a conversion
        {
            **raw,
            "tcap_landmark": {**raw["tcap_landmark"], "source_event_level": "attempt"},
        },  # TCap must not carry the field
        {
            **raw,
            "tef_landmark": {
                **base_tef,
                "source_event_level": "incident",
                "assumed_conversion": 0.0,
                "basis_class": "derived",
            },
        },  # bound
        {
            **raw,
            "tef_landmark": {
                **base_tef,
                "source_event_level": "incident",
                "assumed_conversion": 1.5,
                "basis_class": "derived",
            },
        },  # bound
        {
            **raw,
            "tef_landmark": {
                **base_tef,
                "source_event_level": "incident",
                "assumed_conversion": 0.3,
                "basis_class": "derived",
            },
            "tcap_landmark": {
                **raw["tcap_landmark"],
                "basis_class": "convention",
            },  # so the TCap `cited` rule cannot mask this case
            "citations": [{k: v for k, v in c.items() if k != "locator"} for c in raw["citations"]],
        },  # derived without a located citation (M8-N2)
    ):
        with pytest.raises(ValidationError):
            ThreatCommunitySeed.model_validate(bad)
    # positive control: the converted shape validates
    ok = ThreatCommunitySeed.model_validate(
        {
            **raw,
            "tef_landmark": {
                **base_tef,
                "source_event_level": "incident",
                "assumed_conversion": 0.3,
                "basis_class": "derived",
            },
            "citations": [{"title": "x", "url": "https://example.org/x", "locator": "p. 1"}],
        }
    )  # derived needs a located citation
    assert ok.tef_landmark.assumed_conversion == 0.3


def test_at_least_one_seed_is_a_converted_landmark() -> None:
    """Task 10's conversion-caveat test needs a real non-attempt row; fail loud if Task 1 shipped none."""
    assert any(r.tef_landmark.source_event_level != "attempt" for r in load_threat_community_seed())
