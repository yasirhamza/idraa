from idraa.models.enums import StepUpCategory
from idraa.routes.deps import require_step_up


def test_factory_returns_callable():
    assert callable(require_step_up(StepUpCategory.EXPORTS))
    assert require_step_up(StepUpCategory.EXPORTS) is not require_step_up(StepUpCategory.ADMIN)
