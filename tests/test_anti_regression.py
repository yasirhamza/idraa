"""Anti-regression sentinels for PR iota — control schema reshape.

These tests are permanent fixtures. They guard the contract invariants that
downstream tasks (F9–F26) and future PRs (kappa, lambda) depend on. Any
failure in this file signals that a later change has accidentally reverted
or corrupted a PR iota invariant.

No fixtures, no DB access — these are pure import-and-inspect tests.
"""

from __future__ import annotations

# ── PR iota anti-regression sentinels ─────────────────────────────────────


def test_control_model_lacks_dropped_fields() -> None:
    """Control ORM must NOT have function, control_strength, control_reliability,
    or control_coverage attributes after PR iota (spec §6.4).

    These four columns were dropped in the CFA migration. Their presence would
    indicate either the model was not updated or a later patch accidentally
    re-introduced them.
    """
    from idraa.models.control import Control

    for attr in ("function", "control_strength", "control_reliability", "control_coverage"):
        assert not hasattr(Control, attr), (
            f"Control.{attr} still exists — it was dropped in PR iota (spec §6.4). "
            "This sentinel guards against re-introduction."
        )


def test_controls_metadata_lacks_dropped_columns() -> None:
    """controls SQLAlchemy Table metadata must not contain the 4 dropped columns.

    Checks the ORM metadata (not a live DB). Complements test_control_model_lacks_dropped_fields
    by inspecting the Table object that Alembic autogenerates from.
    """
    from idraa.db import Base
    from idraa.models import Control  # noqa: F401  ensures model is registered in metadata

    table = Base.metadata.tables.get("controls")
    assert table is not None, "controls table not found in SQLAlchemy metadata"
    col_names = {c.name for c in table.columns}
    for dropped in ("function", "control_strength", "control_reliability", "control_coverage"):
        assert dropped not in col_names, (
            f"controls.{dropped} found in SQLAlchemy metadata — "
            "it was dropped in PR iota (spec §6.4)."
        )


def test_control_function_assignment_model_is_importable() -> None:
    """ControlFunctionAssignment ORM must be importable from the models package.

    Guards against accidental deletion of the new model file or removal from
    models/__init__.py.
    """
    from idraa.models import ControlFunctionAssignment
    from idraa.models.control_function_assignment import (
        ControlFunctionAssignment as ControlFunctionAssignmentDirect,
    )

    assert ControlFunctionAssignmentDirect is ControlFunctionAssignment


def test_fair_cam_sub_function_has_exactly_26_members() -> None:
    """FairCamSubFunction must have exactly 26 members (spec §6.2).

    The count is frozen after PR iota ships — slugs appear in serialized run
    snapshots. Adding or removing values requires a spec amendment and a data
    migration. This sentinel is the freeze guard.
    """
    from idraa.models.enums import FairCamSubFunction

    count = len(FairCamSubFunction)
    assert count == 26, (
        f"FairCamSubFunction has {count} members; expected exactly 26. "
        "See spec §6.2 and docs/reference/fair-cam-standard-alignment.md §3 "
        "for the frozen sub-function inventory."
    )
