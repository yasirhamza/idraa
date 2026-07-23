from sqlalchemy import inspect

from idraa.models.enums import StepUpCategory
from idraa.models.security_settings import SecuritySettings


def test_categories():
    assert {c.value for c in StepUpCategory} == {"exports", "destructive", "admin", "credentials"}


def test_columns_nullable_overrides():
    cols = {c.name: c for c in inspect(SecuritySettings).columns}
    assert {
        "mfa_policy",
        "step_up_window_seconds",
        "step_up_exports",
        "step_up_destructive",
        "step_up_admin",
        "step_up_credentials",
        "organization_id",
    } <= set(cols)
    for n in ("mfa_policy", "step_up_window_seconds", "step_up_exports", "step_up_admin"):
        assert cols[n].nullable is True
