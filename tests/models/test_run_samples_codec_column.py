"""RunSamples model — arrays_codec BLOB column + arrays now nullable (Task 2).

Additive nullable ``arrays_codec`` column (compressed binary MC arrays,
services/sample_codec.py, preferred store going forward) alongside the
legacy JSON ``arrays`` column, now made nullable so future writers can skip
it once callers migrate to the codec. Both FKs on run_samples (run_id ->
risk_analysis_runs ON DELETE CASCADE, organization_id -> organizations
ON DELETE RESTRICT via OrgMixin) must survive the SQLite batch-alter
recreate this nullable-column change forces; see
tests/migrations/test_run_samples_codec_migration.py for the DB-level proof.
"""

from __future__ import annotations

import sqlalchemy as sa

from idraa.models.run_samples import RunSamples


def test_arrays_codec_column_is_blob_nullable() -> None:
    col = RunSamples.__table__.c.arrays_codec
    assert isinstance(col.type, sa.LargeBinary)
    assert col.nullable is True


def test_legacy_arrays_column_now_nullable() -> None:
    assert RunSamples.__table__.c.arrays.nullable is True


def test_fk_ondelete_actions_preserved() -> None:
    fks = {fk.column.table.name: fk for fk in RunSamples.__table__.foreign_keys}
    assert fks["risk_analysis_runs"].ondelete == "CASCADE"
    assert fks["organizations"].ondelete == "RESTRICT"
