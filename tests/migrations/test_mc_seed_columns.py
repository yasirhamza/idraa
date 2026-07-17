"""Migration tests for MC-seed reproducibility columns (Phase 1).

Verifies that:
- risk_analysis_runs has a nullable ``random_seed`` (Integer) column.
- run_samples has a nullable ``derived_seed_keys`` (JSON) column.

Both columns are nullable so existing rows remain valid without backfill.
"""

from idraa.models.risk_analysis_run import RiskAnalysisRun
from idraa.models.run_samples import RunSamples


def test_run_has_nullable_random_seed() -> None:
    col = RiskAnalysisRun.__table__.c["random_seed"]
    assert col.nullable is True


def test_run_samples_has_nullable_derived_seed_keys() -> None:
    col = RunSamples.__table__.c["derived_seed_keys"]
    assert col.nullable is True
