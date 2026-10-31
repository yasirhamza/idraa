"""Issue #299 — org-wide META inheritance helpers shared by the control
services (eligibility), the run freeze (services/runs.py), the executor
(run_executor.py), the workbook and the run-detail route.

A scenario that does not link an org-wide control still inherits that
control's VMC/DSC assignments. The executor models this as a META-FILTERED
VARIANT of the control registered under ``variant_id(cid)`` so fair_cam's
composition, attribution and every snapshot-joining consumer treat it as an
ordinary (separate) control. The suffix lives only in JSON payloads — never in
a UUID column or the inputs hash.

``inherits_assignment`` is THE predicate (spec D6): eligibility and the
variant filter both use it, so a control the form accepts as org-wide can
never produce an empty or unresolvable variant.
"""

from __future__ import annotations

from typing import Final

from fair_cam.risk_engine.group_composition import meta_sub_functions

from idraa.models.control import Control
from idraa.models.control_function_assignment import ControlFunctionAssignment

INHERITED_SUFFIX: Final[str] = ":meta"
INHERITED_NAME_SUFFIX: Final[str] = " (org-wide)"


def variant_id(control_id: str) -> str:
    return f"{control_id}{INHERITED_SUFFIX}"


def is_variant_id(any_id: str) -> bool:
    return any_id.endswith(INHERITED_SUFFIX)


def base_control_id(any_id: str) -> str:
    return any_id[: -len(INHERITED_SUFFIX)] if is_variant_id(any_id) else any_id


def _meta_slugs() -> frozenset[str]:
    return frozenset(sf.value for sf in meta_sub_functions())


def inherits_assignment(sub_function: str, source_sub_function: str | None = None) -> bool:
    """True when an assignment is inherited org-wide (spec D6).

    A plain row is inherited iff its sub-function is meta (VMC/DSC). A derived
    ``dsc_corr_misaligned`` row (#268) passes its SOURCE's slug: it is inherited
    only when that source is itself meta (today only ``vmc_corr_implementation``).
    An LEC-sourced derived row is dropped WITH its source — the Response process
    it credits needs an explicit scenario link (D2) — so the resolver never sees
    a stranded link (register C12).
    """
    meta = _meta_slugs()
    if sub_function not in meta:
        return False
    return source_sub_function is None or source_sub_function in meta


def meta_assignments(control: Control) -> list[ControlFunctionAssignment]:
    """The control's inheritable rows. Raises if none: the eligibility
    invariant (services/controls.py) should have prevented this."""
    sf_by_id = {a.id: a.sub_function.value for a in control.assignments}
    rows = [  # adapter-iter: ok — filter keeps every matching row
        a
        for a in control.assignments
        if inherits_assignment(
            a.sub_function.value,
            sf_by_id.get(a.derived_from_assignment_id)
            if a.derived_from_assignment_id is not None
            else None,
        )
    ]
    if not rows:
        raise ValueError(
            f"Control {control.id} is org-wide but has no inheritable VMC/DSC "
            "assignment — eligibility invariant violated upstream"
        )
    return rows
