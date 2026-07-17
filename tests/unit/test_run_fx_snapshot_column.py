from __future__ import annotations

from sqlalchemy import inspect

from idraa.models.risk_analysis_run import RiskAnalysisRun


def test_run_has_presentation_fx_snapshot_column() -> None:
    cols = {c.key for c in inspect(RiskAnalysisRun).columns}
    assert "presentation_fx_snapshot" in cols
    assert inspect(RiskAnalysisRun).columns["presentation_fx_snapshot"].nullable is True
