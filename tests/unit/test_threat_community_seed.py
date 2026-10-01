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
    "nation_state": "283cd7c630a3bcb8594eb623e2aef21a161d39c9ffcca81012b2fae8aaf4c8a2",
    "cybercriminals": "f8e75b662dfebd3796b239d7662f29f8dc5d13ac672c11d527cf98e0c8ddafc5",
    "hacktivists": "87466e7d925cd48eac6ae2ef51fe4b4d066cfad2122c9751af180fdd4f42b703",
    "competitors": "d3753f6f5da25aaa411e095884308adc0a71a72e8a8e448280afa91dbef05626",
    "privileged_insider": "9273fc6ad7dc90d553a3b28c20cbf8108e85db6bdcca292ec7907b75da2f6a92",
    "nonprivileged_insider": "9926b08c995c6cc6dffcde4c6e9c3d3a0de2a0da5966b64988de527a525f614a",
    "insider_accidental": "a5a78ba668eb939fc8441d9a9a9d9ab107ef07d9912816ed2f7a2c8dc1e58288",
    "third_party": "f299573d81dd7d64c2074c277e08b7b769039f875664634c7a8415a4db6784be",
    "opportunistic_hackers": "6469dd926048272c9acff2eaefde152abe56a28949d9d977e9a8c63dd006a16c",
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


@pytest.mark.parametrize("slug", CANONICAL_THREAT_COMMUNITY_SLUGS)
def test_named_statistic_is_coupled_to_its_stated_placement(slug: str) -> None:
    """M2-N1: couples the lead-in's named source statistic to the placement phrase
    stated later in the SAME derivation. The per-row loop above
    (test_privileged_insider_states_control_bypass_and_every_definition_is_control_naive)
    only special-cases the "probability" branch (asserting mean-placement) and
    otherwise just checks that EXACTLY ONE of the two placement phrases is present
    -- it never checks a 'mean'-only lead-in matches mean-placement, and a 'mode'
    lead-in has no coverage at all (no current seed row uses it). This tripwire
    closes both gaps: mean/probability (never mode) -> PERT-mean-placed; mode
    (never mean/probability) -> mode-placed.
    """
    by = {r.slug: r for r in load_threat_community_seed()}
    d_low = by[slug].tef_landmark.derivation.lower()
    lead = re.search(r"source statistic \(([^)]*)\)", d_low)
    assert lead, slug
    stat = lead.group(1)
    names_mean_or_probability = (
        bool(re.search(r"\b(mean|probability)\b", stat)) and "mode" not in stat
    )
    names_mode = "mode" in stat and not re.search(r"\b(mean|probability)\b", stat)
    assert names_mean_or_probability or names_mode, (
        f"{slug}: lead-in names neither mean/probability nor mode (and not both): {stat!r}"
    )
    if names_mean_or_probability:
        assert "placed as the pert mean" in d_low, slug
    if names_mode:
        assert "placed as the mode" in d_low, slug


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


def test_schema_rejects_bad_cases_with_field_named_errors() -> None:
    """spec N2 (PR-gate r1): unlike the ``bad`` tuples above (which only assert SOME
    ValidationError is raised), every case here asserts the error message names the
    field/rule that actually tripped -- a tripwire against a future schema change that
    makes the WRONG check fire first and silently pass this test for the wrong reason."""
    raw_rows = json.loads(Path(SEED_PATH).read_text(encoding="utf-8"))
    raw_tcap = next(r for r in raw_rows if r["tcap_landmark"] is not None)
    raw_non_malicious = next(r for r in raw_rows if r["intent"] == "non_malicious")
    assert raw_non_malicious["tcap_landmark"] is None  # sanity: the fixture this case needs

    cases: list[tuple[dict, str]] = [
        (
            # non-finite value
            {**raw_tcap, "tef_landmark": {**raw_tcap["tef_landmark"], "mode": float("inf")}},
            "finite",
        ),
        (
            # low == high
            {
                **raw_tcap,
                "tef_landmark": {**raw_tcap["tef_landmark"], "low": 1.0, "mode": 1.0, "high": 1.0},
            },
            "low < high",
        ),
        (
            # TEF low <= 0
            {
                **raw_tcap,
                "tef_landmark": {**raw_tcap["tef_landmark"], "low": 0.0, "mode": 0.1, "high": 0.5},
            },
            "tef_landmark.low",
        ),
        (
            # TCap outside [0, 100]
            {
                **raw_tcap,
                "tcap_landmark": {
                    **raw_tcap["tcap_landmark"],
                    "low": 0.0,
                    "mode": 50.0,
                    "high": 150.0,
                },
            },
            "tcap_landmark",
        ),
        (
            # a non-malicious row carrying a TCap
            {**raw_non_malicious, "tcap_landmark": {**raw_tcap["tcap_landmark"]}},
            "tcap_landmark",
        ),
        (
            # blank Text field
            {**raw_tcap, "motive": "   "},
            "motive",
        ),
        (
            # empty citations
            {**raw_tcap, "citations": []},
            "citations",
        ),
        (
            # unknown slug
            {**raw_tcap, "slug": "martians"},
            "slug",
        ),
        (
            # an accidental row whose definition lacks "nothing intervening"
            {**raw_non_malicious, "threat_event_definition": "An error with no qualifying clause."},
            "threat_event_definition",
        ),
    ]
    for bad, expected_substring in cases:
        with pytest.raises(ValidationError) as exc_info:
            ThreatCommunitySeed.model_validate(bad)
        assert expected_substring in str(exc_info.value), (expected_substring, str(exc_info.value))


@pytest.mark.parametrize("slug", CANONICAL_THREAT_COMMUNITY_SLUGS)
def test_no_bayesian_prior_language(slug: str) -> None:
    """spec N6 (PR-gate r1): Idraa deliberately avoids the Bayesian "prior" framing for
    these landmarks (help/threat-agent-library.html's "Landmarks, not inputs" section:
    "Idraa calls these ranges landmarks and deliberately avoids the Bayesian name for a
    starting distribution"). Scans every text field AND every landmark derivation for a
    stray "prior"/"priors", case-insensitive and word-bounded so "priority"/"prioritise"
    don't false-positive."""
    by = {r.slug: r for r in load_threat_community_seed()}
    r = by[slug]
    fields: dict[str, str] = {f: getattr(r, f) for f in _TEXT_FIELDS}
    fields["tef_landmark.derivation"] = r.tef_landmark.derivation
    if r.tcap_landmark is not None:
        fields["tcap_landmark.derivation"] = r.tcap_landmark.derivation
    for field, text in fields.items():
        assert not re.search(r"\bpriors?\b", text, re.IGNORECASE), f"{slug}.{field}: {text!r}"
