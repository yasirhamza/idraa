import pytest

from idraa.models.enums import AssetClass
from idraa.models.threat_community import CANONICAL_THREAT_COMMUNITY_SLUGS
from idraa.services.wizard_questions import (
    ScenarioContext,
    humanize_asset_class,
    humanize_threat_community,
    render_question,
)


def test_every_canonical_threat_community_slug_mapped():
    for slug in CANONICAL_THREAT_COMMUNITY_SLUGS:
        phrase = humanize_threat_community(slug, "Some Name")
        assert phrase and phrase != "an attacker"


def test_every_asset_class_enum_value_mapped():
    for asset in AssetClass:
        phrase = humanize_asset_class(asset)
        assert phrase and phrase != "asset"


def test_threat_community_fallback_for_none():
    assert humanize_threat_community(None, None) == "an attacker"


def test_threat_community_falls_back_to_lowered_name_for_unknown_slug():
    # An org-authored / future community not in the curated phrase dict falls
    # back to its own (lowercased) name rather than the generic "an attacker".
    assert humanize_threat_community("some-future-slug", "Future Community") == "future community"


def test_render_question_tef_substitutes_context():
    ctx = ScenarioContext(
        threat_community_slug="cybercriminals",
        threat_community_name="Cybercriminals",
        threat_community_intent="malicious",
        attack_vector="phishing",
        asset_class=AssetClass.OT_SYSTEMS,
    )
    q = render_question("tef", ctx)
    assert q == (
        "In a typical year, how often might cybercriminals try to "
        "compromise your OT/ICS systems via phishing?"
    )


def test_render_question_tef_humanizes_underscored_attack_vector():
    # attack_vector is stored as an enum slug; the rendered copy must not leak
    # underscores ("via email_phishing" -> "via email phishing").
    ctx = ScenarioContext(
        threat_community_slug="cybercriminals",
        threat_community_name="Cybercriminals",
        threat_community_intent="malicious",
        attack_vector="email_phishing",
        asset_class=AssetClass.OT_SYSTEMS,
    )
    q = render_question("tef", ctx)
    assert "via email phishing?" in q
    assert "_" not in q


def test_render_question_tef_reads_cleanly_when_no_attack_vector():
    ctx = ScenarioContext(
        threat_community_slug="cybercriminals",
        threat_community_name="Cybercriminals",
        threat_community_intent="malicious",
        attack_vector=None,
        asset_class=AssetClass.OT_SYSTEMS,
    )
    q = render_question("tef", ctx)
    assert q == (
        "In a typical year, how often might cybercriminals try to compromise your OT/ICS systems?"
    )
    assert "via" not in q
    assert "  " not in q


def test_render_question_vuln_is_inherent_not_residual():
    """Vulnerability must be elicited as INHERENT (control-naive) susceptibility.

    methodology/vuln-inherent-framing: the FAIR-CAM control layer reduces
    vulnerability separately, so the prompt must NOT ask analysts to net out
    their current controls (that double-counts the control benefit). Guards
    against regressing to the old "get through your current controls" wording.
    """
    ctx = ScenarioContext(None, None, None, None, None)
    q = render_question("vuln", ctx)
    assert q == (
        "If the attempt happens, how likely is the attacker to succeed against "
        "the asset's inherent weaknesses, before any of your mitigating controls?"
    )
    assert "current controls" not in q.lower()
    assert "inherent" in q.lower()
    assert "(0" not in q


def test_render_question_pl_is_context_free():
    ctx = ScenarioContext(None, None, None, None, None)
    q = render_question("pl", ctx)
    assert (
        q
        == "If the attack succeeds, what does the event itself cost you: response, recovery, downtime, replacement?"
    )
    assert "(" not in q


def test_render_question_sl_is_event_conditional():
    # Plan-gate M-N2: SL is event-conditional (matches PL), NOT annualized —
    # FAIR Secondary Loss is a Loss-Magnitude component per loss event.
    ctx = ScenarioContext(None, None, None, None, None)
    q = render_question("sl", ctx)
    assert (
        q
        == "If the attack succeeds, what do other stakeholders' reactions cost you: fines, lost business, and the response they force?"
    )
    assert "(" not in q
    assert "12 months" not in q


def test_render_question_unknown_fieldset_raises():
    ctx = ScenarioContext(None, None, None, None, None)
    with pytest.raises(KeyError):
        render_question("unknown", ctx)


def test_secondary_loss_scaling_guardrail_is_present():
    """Register A1's stated defence is product copy — pin it on both authoring surfaces
    (#174, T6b-Meth IMPORTANT-2)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    form = (root / "src/idraa/templates/scenarios/form.html").read_text(encoding="utf-8")
    wiz = (root / "src/idraa/templates/scenarios/wizard/_fair_params_form_inner.html").read_text(
        encoding="utf-8"
    )
    for text in (form, wiz):
        flat = " ".join(text.split())  # the copy wraps mid-sentence in both templates
        assert "applies secondary loss to every simulated loss event" in flat.lower()
        assert "scale it by the fraction of loss events" in flat


def test_non_malicious_questions_never_say_attack_and_drop_the_vector_clause() -> None:
    ctx = ScenarioContext(
        threat_community_slug="insider_accidental",
        threat_community_name="Accidental insider",
        threat_community_intent="non_malicious",
        attack_vector="privileged_access_misuse",
        asset_class=AssetClass.SYSTEMS,
    )
    for fs in ("tef", "vuln", "pl", "sl"):
        q = render_question(fs, ctx)
        assert "attack" not in q.lower() and "misuse" not in q.lower(), (fs, q)
    assert "make an error involving" in render_question("tef", ctx)


def test_malicious_question_keeps_compromise_wording() -> None:
    ctx = ScenarioContext(
        threat_community_slug="cybercriminals",
        threat_community_name="Cybercriminals",
        threat_community_intent="malicious",
        attack_vector="phishing",
        asset_class=AssetClass.SYSTEMS,
    )
    assert "try to compromise" in render_question(
        "tef", ctx
    ) and "cybercriminals" in render_question("tef", ctx)


_NON_MALICIOUS_CTX = ScenarioContext(
    threat_community_slug="insider_accidental",
    threat_community_name="Accidental insider",
    threat_community_intent="non_malicious",
    attack_vector=None,
    asset_class=None,
)


def test_non_malicious_vuln_question_is_still_inherent_framed() -> None:
    """M-I2: the error-framed Vulnerability copy must keep the inherent-framing
    pin too — methodology/vuln-inherent-framing applies regardless of intent."""
    q = render_question("vuln", _NON_MALICIOUS_CTX)
    assert q == (
        "If that error happens, how likely is it to become a loss given the "
        "asset's inherent weaknesses, before any of your mitigating controls?"
    )
    assert "inherent" in q.lower()
    assert "before any of your mitigating controls" in q
    assert "current controls" not in q.lower()


def test_non_malicious_pl_question_uses_error_framing() -> None:
    q = render_question("pl", _NON_MALICIOUS_CTX)
    assert q == (
        "If the error results in a loss, what does the event itself cost you: "
        "response, recovery, downtime, replacement?"
    )
    assert "If the error results in a loss" in q
    assert "attack" not in q.lower()


def test_non_malicious_sl_question_uses_error_framing() -> None:
    q = render_question("sl", _NON_MALICIOUS_CTX)
    assert q == (
        "If the error results in a loss, what do other stakeholders' reactions "
        "cost you: fines, lost business, and the response they force?"
    )
    assert "If the error results in a loss" in q
    assert "attack" not in q.lower()
