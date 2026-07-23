import dataclasses

from sqlalchemy import inspect

from idraa.models.enums import StepUpCategory
from idraa.models.security_settings import SecuritySettings
from idraa.services.security_settings import _CAT_ATTR, _Snapshot

# The ONLY hardcoded pairs -- everything else below is derived from the live
# StepUpCategory enum / _CAT_ATTR map / SecuritySettings columns / _Snapshot
# fields, so this test breaks if any of those four drift out of sync.
_DIRECT_ORM_TO_SNAPSHOT = {
    "mfa_policy": "mfa_policy",
    "step_up_window_seconds": "step_up_window_seconds",
}


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


def test_snapshot_orm_field_sync_bidirectional():
    """Pins the hand-mirrored mapping in services/security_settings.py
    (_CAT_ATTR + the load_security_settings() field-by-field copy into
    _Snapshot) against the live StepUpCategory enum and SecuritySettings
    model, in both directions.

    Arch-I1 (PR-gate finding): a new override column, a new StepUpCategory
    member, or a new _Snapshot field added without its counterpart is a
    SILENT fail-open -- step_up_required() falls through to its 'default-on'
    True *only* when the cache attr lookup fails to find an override; a
    dropped mapping instead means the override is simply never read, so an
    admin's step-up-off (or MFA-optional) choice for the new category quietly
    never takes effect while everything else keeps working. This test must
    fail the moment any of the four definitions (enum / _CAT_ATTR / ORM
    columns / _Snapshot fields) drifts from the other three.
    """
    orm_cols = {c.key for c in inspect(SecuritySettings).columns}
    snapshot_fields = {f.name for f in dataclasses.fields(_Snapshot)}

    # 1. _CAT_ATTR's keys must be EXACTLY the StepUpCategory members -- not a
    #    subset either direction.
    assert set(_CAT_ATTR.keys()) == set(StepUpCategory), (
        f"_CAT_ATTR keys {sorted(_CAT_ATTR.keys())} != StepUpCategory members "
        f"{sorted(StepUpCategory)} -- a category was added to (or removed "
        f"from) the enum without updating the resolver's _CAT_ATTR map."
    )

    # 2. Derive the ORM-column -> _Snapshot-field mapping the resolver
    #    hand-mirrors: the two direct (non-category) mappings, plus one
    #    step_up_<attr> column per _CAT_ATTR value.
    category_mapping = {f"step_up_{attr}": attr for attr in _CAT_ATTR.values()}
    expected_mapping = {**_DIRECT_ORM_TO_SNAPSHOT, **category_mapping}

    for orm_col, snap_field in expected_mapping.items():
        assert orm_col in orm_cols, (
            f"expected ORM column {orm_col!r} (derived from _CAT_ATTR / the "
            f"direct mapping) is missing from SecuritySettings."
        )
        assert snap_field in snapshot_fields, (
            f"expected _Snapshot field {snap_field!r} (derived from _CAT_ATTR "
            f"/ the direct mapping) is missing from _Snapshot."
        )

    # 3. ORM -> mapping: every step_up_* Boolean override column on the model
    #    (excluding the non-Boolean window column) is accounted for by
    #    category_mapping -- catches a NEW override column added to the model
    #    without a matching StepUpCategory member / _CAT_ATTR entry.
    model_step_up_bool_cols = {
        name
        for name in orm_cols
        if name.startswith("step_up_") and name != "step_up_window_seconds"
    }
    assert model_step_up_bool_cols == set(category_mapping.keys()), (
        f"SecuritySettings step_up_* Boolean columns "
        f"{sorted(model_step_up_bool_cols)} don't match the columns derivable "
        f"from _CAT_ATTR {sorted(category_mapping.keys())} -- a new override "
        f"column was added to the model without a matching StepUpCategory "
        f"member (or vice versa)."
    )

    # 4. Snapshot -> ORM: every _Snapshot field maps back to a real column --
    #    catches a new _Snapshot field added without a backing column (or a
    #    stale field left behind after a column was dropped).
    snapshot_to_orm = {v: k for k, v in expected_mapping.items()}
    assert set(snapshot_to_orm.keys()) == snapshot_fields, (
        f"_Snapshot fields {sorted(snapshot_fields)} don't match fields "
        f"derivable from the ORM<->category mapping "
        f"{sorted(snapshot_to_orm.keys())} -- a _Snapshot field was added "
        f"without a corresponding SecuritySettings column + _CAT_ATTR entry "
        f"(or vice versa)."
    )
    for snap_field, orm_col in snapshot_to_orm.items():
        assert orm_col in orm_cols, (
            f"expected ORM column {orm_col!r} backing _Snapshot field "
            f"{snap_field!r} is missing from SecuritySettings."
        )
