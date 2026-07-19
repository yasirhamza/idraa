# tests/unit/test_wizard_reestimate_seed.py
"""Unit tests for wizard re-elicitation seeding (#56).

seed_wizard_state_from_scenario is pure: it maps a loaded Scenario (+
pre-fetched SME rows and control ids) into a WizardState targeting that
scenario. The SME-row loader is tested at the service/integration level
(Task 4); here we pin the pure mapping, including the adapter-iteration
contract (N>=3 rows survive per fieldset) and loss_shape derivation.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from idraa.services.wizard_state import WizardState, seed_wizard_state_from_scenario


def _scenario(**over):
    base = {
        "id": uuid.uuid4(),
        "row_version": 3,
        "name": "Ransomware on historian",
        "description": "desc",
        "threat_category": SimpleNamespace(value="ransomware"),
        "threat_actor_type": SimpleNamespace(value="organized_crime"),
        "asset_class": SimpleNamespace(value="ot_systems"),
        "attack_vector": "phishing",
        "vuln_framing": "inherent",
        "primary_loss": {"distribution": "PERT", "low": 1.0, "mode": 2.0, "high": 3.0},
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_seed_targets_scenario_and_captures_row_version():
    s = _scenario()
    st = seed_wizard_state_from_scenario(
        s, sme_estimates={}, mitigating_control_ids=[], tx_id="deadbeef"
    )
    assert st.target_scenario_id == s.id.hex
    assert st.target_expected_row_version == 3
    assert st.current_step == 2
    assert st.tx_id == "deadbeef"
    # Library fields stay None: no pin is involved in a re-estimation.
    assert st.library_entry_id is None and st.override_id is None


def test_seed_copies_descriptive_fields_and_controls():
    cid = uuid.uuid4()
    st = seed_wizard_state_from_scenario(
        _scenario(),
        sme_estimates={},
        mitigating_control_ids=[str(cid)],
        tx_id="t",
    )
    assert st.name == "Ransomware on historian"
    assert st.threat_category == "ransomware"
    assert st.threat_actor_type == "organized_crime"
    assert st.asset_class == "ot_systems"
    assert st.attack_vector == "phishing"
    assert st.mitigating_control_ids == [str(cid)]


def test_seed_loss_shape_derivation():
    pert = _scenario()
    logn = _scenario(primary_loss={"distribution": "lognormal", "mean": 10.0, "sigma": 1.0})
    mix = _scenario(
        primary_loss={
            "distribution": "lognormal_mixture",
            "components": [{"mean": 10.0, "sigma": 1.0, "weight": 1.0}],
        }
    )
    assert (
        seed_wizard_state_from_scenario(
            pert, sme_estimates={}, mitigating_control_ids=[], tx_id="t"
        ).loss_shape
        == "capped"
    )
    for s in (logn, mix):
        assert (
            seed_wizard_state_from_scenario(
                s, sme_estimates={}, mitigating_control_ids=[], tx_id="t"
            ).loss_shape
            == "catastrophic"
        )


def test_seed_preserves_all_sme_rows_per_fieldset():
    # Adapter-iteration contract (CLAUDE.md): N>=3 rows survive intact,
    # including the sme_id XOR sme_name identity shape.
    rows = {
        "tef": [
            {"sme_id": str(uuid.uuid4()), "low": 0.1, "high": 2.0},
            {"sme_name": "Alice", "low": 0.2, "high": 3.0},
            {"sme_name": "Bob", "low": 0.3, "high": 4.0},
        ]
    }
    st = seed_wizard_state_from_scenario(
        _scenario(), sme_estimates=rows, mitigating_control_ids=[], tx_id="t"
    )
    assert st.sme_estimates == rows


def test_legacy_residual_scenario_never_rehydrates_vuln_rows():
    # Meth-B1: pre-#339 vuln rows embed a control discount; rehydrating them
    # would make the finalize "inherent" stamp a lie. tef/pl/sl unaffected.
    rows = {
        "tef": [{"sme_name": "A", "low": 0.1, "high": 2.0}],
        "vuln": [{"sme_name": "A", "low": 0.05, "high": 0.4}],
        "pl": [{"sme_name": "A", "low": 1e4, "high": 1e6}],
    }
    st = seed_wizard_state_from_scenario(
        _scenario(vuln_framing="legacy_residual"),
        sme_estimates=rows,
        mitigating_control_ids=[],
        tx_id="t",
    )
    assert "vuln" not in st.sme_estimates
    assert set(st.sme_estimates) == {"tef", "pl"}


def test_inherent_scenario_rehydrates_all_fieldsets():
    rows = {"vuln": [{"sme_name": "A", "low": 0.05, "high": 0.4}]}
    st = seed_wizard_state_from_scenario(
        _scenario(), sme_estimates=rows, mitigating_control_ids=[], tx_id="t"
    )
    assert st.sme_estimates == rows


def test_legacy_state_json_without_target_keys_deserializes_to_none():
    # The whitelist loader drops unknown keys and dataclass defaults fill
    # missing ones — a pre-#56 draft must load with target fields None.
    st = WizardState(tx_id="t")
    assert st.target_scenario_id is None
    assert st.target_expected_row_version is None
