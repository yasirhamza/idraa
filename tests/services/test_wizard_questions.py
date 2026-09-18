import pytest

from idraa.models.enums import AssetClass, ThreatActorType
from idraa.services.wizard_questions import (
    ScenarioContext,
    humanize_asset_class,
    humanize_threat_actor,
    render_question,
)


def test_every_threat_actor_enum_value_mapped():
    for actor in ThreatActorType:
        phrase = humanize_threat_actor(actor)
        assert phrase and phrase != "an attacker"


def test_every_asset_class_enum_value_mapped():
    for asset in AssetClass:
        phrase = humanize_asset_class(asset)
        assert phrase and phrase != "asset"


def test_threat_actor_fallback_for_none():
    assert humanize_threat_actor(None) == "an attacker"


def test_render_question_tef_substitutes_context():
    ctx = ScenarioContext(
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
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
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
        attack_vector="email_phishing",
        asset_class=AssetClass.OT_SYSTEMS,
    )
    q = render_question("tef", ctx)
    assert "via email phishing?" in q
    assert "_" not in q


def test_render_question_tef_reads_cleanly_when_no_attack_vector():
    ctx = ScenarioContext(
        threat_actor_type=ThreatActorType.CYBERCRIMINALS,
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
    ctx = ScenarioContext(None, None, None)
    q = render_question("vuln", ctx)
    assert q == (
        "If the attempt happens, how likely is the attacker to succeed against "
        "the asset's inherent weaknesses, before any of your mitigating controls?"
    )
    assert "current controls" not in q.lower()
    assert "inherent" in q.lower()
    assert "(0" not in q


def test_render_question_pl_is_context_free():
    ctx = ScenarioContext(None, None, None)
    q = render_question("pl", ctx)
    assert (
        q
        == "If the attack succeeds, what does the event itself cost you: response, recovery, downtime, replacement?"
    )
    assert "(" not in q


def test_render_question_sl_is_event_conditional():
    # Plan-gate M-N2: SL is event-conditional (matches PL), NOT annualized —
    # FAIR Secondary Loss is a Loss-Magnitude component per loss event.
    ctx = ScenarioContext(None, None, None)
    q = render_question("sl", ctx)
    assert (
        q
        == "If the attack succeeds, what do other stakeholders' reactions cost you: fines, lost business, and the response they force?"
    )
    assert "(" not in q
    assert "12 months" not in q


def test_render_question_unknown_fieldset_raises():
    ctx = ScenarioContext(None, None, None)
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
