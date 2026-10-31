"""fair_cam's meta_sub_functions() and the app's slug-prefix domain decoder
must agree, or the org-wide eligibility rule (#299) would disagree with the
engine's composition routing."""

from fair_cam.models.sub_function import FairCamSubFunction as FcSF
from fair_cam.risk_engine.group_composition import meta_sub_functions

from idraa.models.enums import ControlDomain, FairCamSubFunction, subfunction_to_domain


def test_meta_sub_functions_agrees_with_subfunction_to_domain():
    app_meta = {
        sf.value
        for sf in FairCamSubFunction
        if subfunction_to_domain(sf) != ControlDomain.LOSS_EVENT
    }
    fc_meta = {sf.value for sf in meta_sub_functions()}
    assert app_meta == fc_meta
    assert {sf.value for sf in FcSF} == {sf.value for sf in FairCamSubFunction}
