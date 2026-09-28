"""criteria.json must cover every enum value Idraa uses, with complete, reconciled wording."""

from __future__ import annotations

import json

from scripts.curation_check.criteria import (
    NONE_COVERED,
    NONE_FITS,
    TAXONOMY_FIELDS,
    control_function_questions,
    library_match_question,
    load_criteria,
    scenario_label_questions,
)

from idraa.models.enums import AssetClass, FairCamSubFunction, ThreatActorType, ThreatCategory


def test_criteria_cover_every_enum_value() -> None:
    c = load_criteria()
    assert set(c.subfunctions) == {
        f.value for f in FairCamSubFunction if f is not FairCamSubFunction.DSC_CORR_MISALIGNED
    }
    assert set(c.taxonomy["threat_event_type"]) == {v.value for v in ThreatCategory}
    assert set(c.taxonomy["asset_class"]) == {v.value for v in AssetClass}
    assert set(c.taxonomy["threat_actor_type"]) == {v.value for v in ThreatActorType}


def test_every_option_has_complete_wording() -> None:
    c = load_criteria()
    for slug, s in c.subfunctions.items():
        assert s["label"] and s["what"] and s["not_for"], slug
        assert len(s["examples"]) >= 3, slug
        assert s["domain"] in {"LEC", "VMC", "DSC"}, slug
    for field in TAXONOMY_FIELDS:
        for value, t in c.taxonomy[field].items():
            assert t["what"] and t["not_for"], (field, value)


def test_per_behaviour_rule_replaces_the_mandate_rule() -> None:  # M-B1
    c = load_criteria()
    assert "only if the control itself performs or technically enforces" in c.convention  # N-I1r2
    assert "mandating" not in json.dumps(c.subfunctions)
    assert "Correcting misaligned decisions is not offered" in c.convention


def test_scenario_label_questions_offer_every_value_plus_none() -> None:
    qs = scenario_label_questions(load_criteria())
    assert set(qs) == set(TAXONOMY_FIELDS)
    assert all(q["type"] == "choice" for q in qs.values())
    assert set(qs["threat_actor_type"]["criteria"]) == {v.value for v in ThreatActorType} | {
        NONE_FITS
    }


def test_threat_event_texts_do_not_contradict_the_precedence() -> None:  # M-I1r4
    text = json.dumps(load_criteria().taxonomy["threat_event_type"])
    for phrase in (
        "unless an insider",
        "by any means",
        "privileged vendor",
        "Use insider_misuse when the actor already held",
    ):
        assert phrase not in text, phrase


def test_threat_event_question_carries_the_precedence_rule() -> None:  # M-I2
    qs = scenario_label_questions(load_criteria())
    assert qs["threat_event_type"]["instructions"]["convention"].startswith(
        "Pick by this precedence"
    )
    assert "convention" not in qs["asset_class"]["instructions"]


def test_control_function_questions_are_one_noul_per_function() -> None:
    qs = control_function_questions(load_criteria())
    assert len(qs) == 25
    assert all(k.startswith("fn:") and q["type"] == "noul" for k, q in qs.items())
    assert "fn:dsc_corr_misaligned" not in qs
    assert set(qs["fn:lec_prev_resistance"]["criteria"]) == {"true", "false"}


def test_library_match_question_appends_none_and_optional_definition() -> None:
    q = library_match_question({"A": "desc a"}, "Which?", NONE_COVERED, "No match.")
    assert q["criteria"] == {"A": "desc a", NONE_COVERED: "No match."}
    assert "definition" not in q["instructions"]
    q2 = library_match_question(
        {"A": "desc a"}, "Which?", NONE_COVERED, "No match.", definition="Same risk means…"
    )
    assert q2["instructions"]["definition"] == "Same risk means…"


def test_criteria_hash_is_stable() -> None:
    a, b = load_criteria(), load_criteria()
    assert a.sha256 == b.sha256
    assert len(a.sha256) == 64
