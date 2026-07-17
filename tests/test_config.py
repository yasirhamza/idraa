import pytest
from pydantic import ValidationError

from idraa.config import Settings


def test_verification_workbook_scenario_cap_default():
    s = Settings(environment="test")
    assert s.verification_workbook_max_scenarios == 15


def test_verification_workbook_scenario_cap_env_override(monkeypatch):
    monkeypatch.setenv("VERIFICATION_WORKBOOK_MAX_SCENARIOS", "8")
    s = Settings(environment="test")
    assert s.verification_workbook_max_scenarios == 8


def test_verification_workbook_max_rows_field_removed():
    # Task 7: the explicit-row generator was removed; the misleadingly-named
    # verification_workbook_max_rows field went with it (replaced by
    # verification_workbook_max_n for the LET path).
    s = Settings(environment="test")
    assert not hasattr(s, "verification_workbook_max_rows")


# --- Task 5: responsiveness caps (max_n + aggregate ΣN cap) -------------------


def test_verification_workbook_responsiveness_caps_defaults():
    s = Settings(environment="test")
    assert s.verification_workbook_max_n == 50_000
    assert s.verification_workbook_aggregate_total_max == 150_000


def test_verification_workbook_responsiveness_caps_env_override(monkeypatch):
    monkeypatch.setenv("VERIFICATION_WORKBOOK_MAX_N", "20000")
    monkeypatch.setenv("VERIFICATION_WORKBOOK_AGGREGATE_TOTAL_MAX", "300000")
    s = Settings(environment="test")
    assert s.verification_workbook_max_n == 20_000
    assert s.verification_workbook_aggregate_total_max == 300_000


def test_verification_workbook_max_n_rejects_out_of_bounds(monkeypatch):
    # ge=100, le=100_000. Fields carry an alias, so validation is exercised via the
    # env-var (alias) path — the canonical override mechanism (matches the override
    # test above).
    monkeypatch.setenv("VERIFICATION_WORKBOOK_MAX_N", "99")
    with pytest.raises(ValidationError):
        Settings(environment="test")
    monkeypatch.setenv("VERIFICATION_WORKBOOK_MAX_N", "100001")
    with pytest.raises(ValidationError):
        Settings(environment="test")


def test_verification_workbook_aggregate_total_max_rejects_out_of_bounds(monkeypatch):
    # ge=1_000, le=500_000.
    monkeypatch.setenv("VERIFICATION_WORKBOOK_AGGREGATE_TOTAL_MAX", "999")
    with pytest.raises(ValidationError):
        Settings(environment="test")
    monkeypatch.setenv("VERIFICATION_WORKBOOK_AGGREGATE_TOTAL_MAX", "500001")
    with pytest.raises(ValidationError):
        Settings(environment="test")


def test_verification_workbook_aggregate_total_max_bounds_admit_the_150k_default(monkeypatch):
    # Regression (Arch-I-A): the 150_000 default MUST pass the le bound — the old
    # field's le=100_000 would have failed it at boot.
    s = Settings(environment="test")
    assert s.verification_workbook_aggregate_total_max == 150_000
    # And an explicit 150_000 is accepted (no ValidationError).
    monkeypatch.setenv("VERIFICATION_WORKBOOK_AGGREGATE_TOTAL_MAX", "150000")
    assert Settings(environment="test").verification_workbook_aggregate_total_max == 150_000
