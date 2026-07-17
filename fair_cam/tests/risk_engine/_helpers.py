"""Shared test factories for #130 engine weak-AND composition tests.

`make_control` / `make_risk_parameters` wrap the real fair_cam constructors so
Tasks 2/5/6 build inputs uniformly (plan-gate B-spec-2). This replaces the
ad-hoc module-local `_make_control` in `test_compose_group_effectiveness.py`.

NOT a test module itself — the leading underscore keeps pytest from collecting
it.
"""

from __future__ import annotations

from fair_cam.models.control import (
    Control,
    ControlType,
    CostModel,
    FairCamControlFunctionAssignment,
    subfunction_to_domain,
)
from fair_cam.models.sub_function import (
    SUB_FUNCTION_UNITS,
    FairCamSubFunction,
    UnitType,
)
from fair_cam.risk_engine.fair_core import (
    DistributionType,
    FAIRDistribution,
    FAIRParameters,
)

# Each assignment is (sub_function_str, unit_str, capability_value).
AssignmentSpec = tuple[str, str, float | None]


def make_control(
    *,
    assignments: list[AssignmentSpec],
    control_id: str = "c1",
    coverage: float = 1.0,
    reliability: float = 1.0,
    control_type: ControlType = ControlType.TECHNICAL,
) -> Control:
    """Build a real `Control` from `(sub_function, unit, capability)` tuples.

    `unit_str` is asserted against the sub-function's canonical
    `SUB_FUNCTION_UNITS` entry so a mistyped unit in a test spec fails loudly
    rather than silently changing the opeff branch.

    Domain is derived from the first assignment's sub-function
    (`subfunction_to_domain`); all assignments are expected to share a domain.
    Default `coverage`/`reliability` are 1.0 so the assignment's opeff reflects
    the raw capability unless a test overrides them.
    """
    built: list[FairCamControlFunctionAssignment] = []
    for sf_str, unit_str, cap in assignments:
        sf = FairCamSubFunction(sf_str)
        expected_unit = SUB_FUNCTION_UNITS[sf]
        given_unit = UnitType(unit_str)
        if given_unit != expected_unit:
            raise ValueError(
                f"unit mismatch for {sf.value!r}: spec says {given_unit.value!r} "
                f"but SUB_FUNCTION_UNITS says {expected_unit.value!r}"
            )
        built.append(
            FairCamControlFunctionAssignment(
                sub_function=sf,
                capability_value=cap,
                coverage=coverage,
                reliability=reliability,
            )
        )

    domain = subfunction_to_domain(built[0].sub_function)
    return Control(
        control_id=control_id,
        name=control_id,
        description="test",
        domain=domain,
        control_type=control_type,
        cost_model=CostModel(),
        assignments=built,
    )


def make_fair_parameters(
    *,
    tef: float,
    vuln: float,
    primary: float,
    secondary: float,
) -> FAIRParameters:
    """Native-engine analogue of ``make_risk_parameters``.

    Mirrors the same PERT-triple geometry (mode-centred, fixed low/high
    fractions) so a test re-pointed from the (removed) pyfair
    ``ControlAwareRiskCalculator`` to ``NativeControlAwareRiskCalculator``
    keeps an equivalent scenario shape. Vulnerability is a point-mass
    (``UNIFORM{v, v}``) mirroring the old scalar ``vulnerability`` constant.
    """

    def _pert(mode: float) -> FAIRDistribution:
        return FAIRDistribution(
            DistributionType.PERT,
            {"low": mode * 0.5, "mode": mode, "high": mode * 1.5},
        )

    return FAIRParameters(
        threat_event_frequency=_pert(tef),
        vulnerability=FAIRDistribution(DistributionType.UNIFORM, {"low": vuln, "high": vuln}),
        primary_loss=_pert(primary),
        secondary_loss=_pert(secondary),
    )
