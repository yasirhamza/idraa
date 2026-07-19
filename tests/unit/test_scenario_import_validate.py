from __future__ import annotations

from typing import Any

from idraa.services.scenario_import import _validate_rows


def _fd(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "S",
        "description": None,
        "scenario_type": "custom",
        "threat_category": "ransomware",
        "threat_actor_type": "cybercriminals",
        "attack_vector": None,
        "asset_class": "systems",
        "version": "1.0",
        "status": "active",
        "threat_event_frequency": {"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 2},
        "vulnerability": {"distribution": "PERT", "low": 0.2, "mode": 0.35, "high": 0.6},
        "primary_loss": {"distribution": "PERT", "low": 100000, "mode": 1000000, "high": 15000000},
        "secondary_loss": None,
    }
    base.update(over)
    return base


def test_valid_row_becomes_create() -> None:
    preview, errors, forms, _, _am = _validate_rows([(2, _fd())], existing_names=set())
    assert errors == []
    assert preview[0]["action"] == "create"
    assert forms[0] is not None and forms[0].name == "S"


def test_existing_name_skipped() -> None:
    preview, errors, forms, _, _am = _validate_rows([(2, _fd(name="Dup"))], existing_names={"dup"})
    assert preview[0]["action"] == "skip"
    assert forms[0] is None
    assert errors == []  # skip is not an error


def test_intra_file_duplicate_name_skipped() -> None:
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(name="A")), (3, _fd(name="A"))], existing_names=set()
    )
    assert preview[0]["action"] == "create"
    assert preview[1]["action"] == "skip"
    assert forms[1] is None


def test_bad_threat_category_is_error() -> None:
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(threat_category="not_a_category"))], existing_names=set()
    )
    assert preview[0]["action"] == "error"
    assert forms[0] is None
    assert errors and errors[0]["column"] == "threat_category"


def test_pert_low_gt_mode_is_error() -> None:
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss={"distribution": "PERT", "low": 9, "mode": 2, "high": 3}))],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"
    assert errors and "primary_loss" in errors[0]["column"]


def test_pert_mode_gt_high_is_error() -> None:  # SC-I8
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss={"distribution": "PERT", "low": 1, "mode": 9, "high": 3}))],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"


def test_negative_loss_is_error() -> None:  # SC-I8
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss={"distribution": "PERT", "low": -5, "mode": 2, "high": 3}))],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"


def test_vuln_above_one_is_error() -> None:  # B1: now caught by validate_fair_distributions
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(vulnerability={"distribution": "PERT", "low": 0.1, "mode": 0.5, "high": 1.5}))],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"


def test_vuln_below_zero_is_error() -> None:  # B1 / SC-I8
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(vulnerability={"distribution": "PERT", "low": -0.1, "mode": 0.5, "high": 0.9}))],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"


def test_non_pert_distribution_is_error() -> None:  # I2 / Meth-I1
    preview, errors, forms, _, _am = _validate_rows(
        [
            (
                2,
                _fd(
                    threat_event_frequency={
                        "distribution": "lognormal",
                        "low": 1,
                        "mode": 2,
                        "high": 3,
                    }
                ),
            )
        ],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"
    assert errors and "distribution" in errors[0]["column"]


def test_extra_key_in_distribution_dict_is_error() -> None:  # B4 / Sec-B2
    preview, errors, forms, _, _am = _validate_rows(
        [
            (
                2,
                _fd(
                    primary_loss={
                        "distribution": "PERT",
                        "low": 1,
                        "mode": 2,
                        "high": 3,
                        "junk": "x" * 100,
                    }
                ),
            )
        ],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"


def test_non_numeric_pert_value_is_error() -> None:
    preview, errors, forms, _, _am = _validate_rows(
        [
            (
                2,
                _fd(
                    threat_event_frequency={
                        "distribution": "PERT",
                        "low": "x",
                        "mode": 2,
                        "high": 3,
                    }
                ),
            )
        ],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"


def test_missing_name_is_error() -> None:
    preview, errors, forms, _, _am = _validate_rows([(2, _fd(name=""))], existing_names=set())
    assert preview[0]["action"] == "error"
    assert errors and errors[0]["column"] in {"name", ""}


def test_extra_smuggled_field_rejected() -> None:
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(organization_id="11111111-1111-1111-1111-111111111111"))],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"


def test_inf_high_in_primary_loss_is_error() -> None:  # Meth-B1
    preview, errors, forms, _, _am = _validate_rows(
        [
            (
                2,
                _fd(
                    primary_loss={
                        "distribution": "PERT",
                        "low": 100000,
                        "mode": 1000000,
                        "high": float("inf"),
                    }
                ),
            )
        ],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"
    assert forms[0] is None


def test_csv_1e999_cell_rejected_end_to_end() -> None:  # Meth-B1 (CSV parser → inf)
    from idraa.services.scenario_import_parsers import _num

    parsed = _num("1e999")
    assert parsed == float("inf")
    preview, errors, forms, _, _am = _validate_rows(
        [
            (
                2,
                _fd(
                    primary_loss={
                        "distribution": "PERT",
                        "low": 100000,
                        "mode": 1000000,
                        "high": parsed,
                    }
                ),
            )
        ],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"


def test_json_1e999_literal_rejected_end_to_end() -> None:  # Meth-B1 (JSON → inf)
    import json

    parsed = json.loads("1e999")
    assert parsed == float("inf")
    preview, errors, forms, _, _am = _validate_rows(
        [
            (
                2,
                _fd(
                    threat_event_frequency={
                        "distribution": "PERT",
                        "low": 0.1,
                        "mode": 0.5,
                        "high": parsed,
                    }
                ),
            )
        ],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"


def test_draft_status_row_creates_as_draft() -> None:
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(status="draft"))], existing_names=set()
    )
    assert preview[0]["action"] == "create"
    assert forms[0] is not None and forms[0].status == "draft"


def test_non_creatable_status_row_errors_at_preview() -> None:
    # Create-domain parity: 'deprecated'/'deleted' pass EntityStatus enum
    # membership but ScenarioService._stamp_new_scenario refuses them — the
    # preview must say so instead of letting the row fail at apply.
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(status="deprecated"))], existing_names=set()
    )
    assert preview[0]["action"] == "error"
    assert errors and errors[0]["column"] == "status"
    assert forms[0] is None


# --- Epic B (#326) Step 3d: lognormal structural + numeric-type guard ---------
# These drive the FULL _validate_rows pipeline (NOT just the parser) — the §2.5
# structural guard is THE security enforcement point. Meth-B1 / Sec-I1 / Sec-I2.


def _lognormal(mean: object = 6.9, sigma: object = 1.0) -> dict[str, object]:
    return {"distribution": "lognormal", "mean": mean, "sigma": sigma}


def test_valid_lognormal_pl_becomes_create() -> None:
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal()))], existing_names=set()
    )
    assert errors == []
    assert preview[0]["action"] == "create"
    assert forms[0] is not None


def test_valid_lognormal_tef_and_sl_become_create() -> None:
    # lognormal allowed on tef / pl / sl.
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(threat_event_frequency=_lognormal(mean=0.0), secondary_loss=_lognormal()))],
        existing_names=set(),
    )
    assert errors == []
    assert preview[0]["action"] == "create"


def test_lognormal_vulnerability_is_rejected() -> None:
    # vuln must always be PERT — lognormal not allowed for vulnerability.
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(vulnerability=_lognormal(mean=-1.0, sigma=0.5)))],
        existing_names=set(),
    )
    assert preview[0]["action"] == "error"
    assert forms[0] is None
    assert errors and "vulnerability" in errors[0]["column"]


def test_lognormal_mean_inf_is_error_never_stored() -> None:  # Meth-B1
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(mean=float("inf"))))], existing_names=set()
    )
    assert preview[0]["action"] == "error"
    assert forms[0] is None


def test_lognormal_1e999_mean_is_error() -> None:  # Meth-B1 (1e999 -> inf)
    parsed = float("1e999")
    assert parsed == float("inf")
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(mean=parsed)))], existing_names=set()
    )
    assert preview[0]["action"] == "error"


def test_lognormal_sigma_zero_is_error() -> None:  # Sec-I2
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(sigma=0)))], existing_names=set()
    )
    assert preview[0]["action"] == "error"
    assert forms[0] is None


def test_lognormal_sigma_negative_is_error() -> None:  # Sec-I2
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(sigma=-1)))], existing_names=set()
    )
    assert preview[0]["action"] == "error"


def test_lognormal_sigma_above_bound_is_error() -> None:  # Sec-I2 (sigma=50 > 10)
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(sigma=50)))], existing_names=set()
    )
    assert preview[0]["action"] == "error"


def test_lognormal_mean_non_numeric_is_error() -> None:  # Sec-I1 (numeric-type guard)
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(mean="abc")))], existing_names=set()
    )
    assert preview[0]["action"] == "error"
    assert forms[0] is None


def test_lognormal_sigma_list_is_error() -> None:  # Sec-I1 (numeric-type guard)
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(sigma=[1, 2])))], existing_names=set()
    )
    assert preview[0]["action"] == "error"


def test_lognormal_bool_sigma_is_error() -> None:  # Sec-I1 (bool is not numeric)
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=_lognormal(sigma=True)))], existing_names=set()
    )
    assert preview[0]["action"] == "error"


def test_lognormal_extra_key_is_error() -> None:  # anti-blob-smuggling preserved
    bad = {"distribution": "lognormal", "mean": 6.9, "sigma": 1.0, "junk": "x" * 100}
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=bad))], existing_names=set()
    )
    assert preview[0]["action"] == "error"


def test_lognormal_missing_sigma_is_error() -> None:  # exact-key-set preserved
    bad = {"distribution": "lognormal", "mean": 6.9}
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=bad))], existing_names=set()
    )
    assert preview[0]["action"] == "error"


def test_unknown_distribution_kind_is_error() -> None:
    bad = {"distribution": "weibull", "low": 1, "mode": 2, "high": 3}
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(primary_loss=bad))], existing_names=set()
    )
    assert preview[0]["action"] == "error"


# --- Slice 1: effect (C/I/A) enum validation ---------------------------------


def test_enum_ok_validates_effect_membership() -> None:
    from idraa.models.enums import ScenarioEffect
    from idraa.services.scenario_import import _enum_ok

    assert _enum_ok("availability", ScenarioEffect) is True
    assert _enum_ok("confidentiality", ScenarioEffect) is True
    assert _enum_ok("integrity", ScenarioEffect) is True
    assert _enum_ok("nope", ScenarioEffect) is False
    assert _enum_ok(None, ScenarioEffect) is True  # optional field: None is valid


def test_invalid_effect_is_error() -> None:
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(effect="not_a_cia_value"))], existing_names=set()
    )
    assert preview[0]["action"] == "error"
    assert forms[0] is None
    assert errors and errors[0]["column"] == "effect"


def test_none_effect_passes_validation() -> None:
    """Absent/None effect is valid — effect is optional (detection-gated default)."""
    preview, errors, forms, _, _am = _validate_rows([(2, _fd(effect=None))], existing_names=set())
    assert errors == []
    assert preview[0]["action"] == "create"
    assert forms[0] is not None
    assert forms[0].effect is None


def test_valid_effect_passes_validation() -> None:
    preview, errors, forms, _, _am = _validate_rows(
        [(2, _fd(effect="availability"))], existing_names=set()
    )
    assert errors == []
    assert preview[0]["action"] == "create"
    assert forms[0] is not None and forms[0].effect == "availability"
