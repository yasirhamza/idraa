"""run_samples + system_state split

Revision ID: 7026c068bbaa
Revises: bf920a18ef0c
Create Date: 2026-05-31 12:00:11.393089

Creates two tables (#297):
- ``run_samples`` — the heavy per-iteration Monte Carlo sample arrays, split
  off ``risk_analysis_runs.simulation_results``. 1:1 with the run (``run_id``
  is both PK and FK ON DELETE CASCADE). Loaded only for full-distribution
  plotting / CSV export, never on list/dashboard paths (#294).
- ``system_state`` — single-row-per-org operational scheduler state
  (``last_retention_sweep_at``). UNIQUE org_id (``uq_system_state_org``).

Then backfills: for every existing run with a non-null ``simulation_results``
blob, split the heavy sample arrays into a new ``run_samples`` row and rewrite
the run's blob to the slim summary. Keyset-paginated in small batches so the
peak memory ceiling is ``_BATCH × max_payload`` (the live DB has multi-MB
rows, up to ~38 MB — see #294), not the whole table at once.

FROZEN SPLIT/MERGE: ``_split`` / ``_merge`` / ``_pop_array`` below are an
intentional, FROZEN copy of ``services/simulation_payload.py`` as of
2026-05-31; do NOT import the live helper. A migration is pinned to a schema
revision: if the HEAD helper's topology later changes, importing it would
corrupt a re-run of this migration on a fresh DB. The copy is duplicated on
purpose so the backfill logic is forever stable at this revision.
"""
import uuid
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7026c068bbaa'
down_revision: Union[str, Sequence[str], None] = 'bf920a18ef0c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# FROZEN copy — intentionally duplicates services/simulation_payload.py as of
# 2026-05-31; do NOT import the live helper. (See module docstring.)
# ---------------------------------------------------------------------------
import copy as _copy  # noqa: E402

_SAMPLE_ARRAY_KEY = "simulation_results"
_RISK_CONTAINERS = (
    "base_risk",
    "residual_risk",
    "aggregate_with_controls",
    "aggregate_without_controls",
)


def _pop_array(container: Any, path: str, out: dict[str, Any]) -> None:
    if isinstance(container, dict) and _SAMPLE_ARRAY_KEY in container:
        out[path] = container.pop(_SAMPLE_ARRAY_KEY)


def _split(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = _copy.deepcopy(payload)
    arrays: dict[str, Any] = {}
    for key in _RISK_CONTAINERS:
        _pop_array(summary.get(key), key, arrays)
    per_scenario = summary.get("per_scenario")
    if isinstance(per_scenario, list):
        for i, entry in enumerate(per_scenario):
            if isinstance(entry, dict):
                _pop_array(entry.get("base_risk"), f"per_scenario/{i}/base_risk", arrays)
                _pop_array(
                    entry.get("residual_risk"), f"per_scenario/{i}/residual_risk", arrays
                )
    return summary, arrays


def _merge(summary: dict[str, Any], arrays: dict[str, Any] | None) -> dict[str, Any]:
    merged = _copy.deepcopy(summary)
    if not arrays:
        return merged
    for path, array in arrays.items():
        parts = path.split("/")
        target: Any = merged
        for p in parts:
            target = target[int(p)] if p.isdigit() else target[p]
        target[_SAMPLE_ARRAY_KEY] = array
    return merged


# Keyset batch size. Peak backfill memory ceiling is ``_BATCH × max_payload``
# (live DB max payload ~38 MB, #294): 10 × 38 MB ≈ 380 MB worst case, well
# under the deployment VM's memory. Bump down if larger payloads appear.
_BATCH = 10


# Lightweight schema-pinned table defs (NOT the ORM models — migrations must
# be schema-pinned so a future model change can't retroactively alter this
# revision's behaviour).
_runs = sa.table(
    "risk_analysis_runs",
    sa.column("id", sa.Uuid()),
    sa.column("organization_id", sa.Uuid()),
    sa.column("simulation_results", sa.JSON()),
)
_samples = sa.table(
    "run_samples",
    sa.column("run_id", sa.Uuid()),
    sa.column("organization_id", sa.Uuid()),
    sa.column("arrays", sa.JSON()),
    sa.column("created_at", sa.DateTime(timezone=True)),
)
_system_state = sa.table(
    "system_state",
    sa.column("id", sa.Uuid()),
    sa.column("organization_id", sa.Uuid()),
    sa.column("last_retention_sweep_at", sa.DateTime(timezone=True)),
)
_organizations = sa.table("organizations", sa.column("id", sa.Uuid()))


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "run_samples",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("arrays", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["risk_analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index(
        op.f("ix_run_samples_organization_id"),
        "run_samples",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "system_state",
        sa.Column("last_retention_sweep_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_system_state_org"),
    )
    op.create_index(
        op.f("ix_system_state_organization_id"),
        "system_state",
        ["organization_id"],
        unique=False,
    )

    bind = op.get_bind()

    # Backfill: split heavy sample arrays out of each run's simulation_results
    # into a run_samples row, rewrite the run blob to the slim summary. Keyset
    # pagination (id > last), None-guarded first iteration (no literal UUID
    # seed — type-agnostic so it works for any PK type), small batch.
    last = None
    while True:
        q = sa.select(
            _runs.c.id, _runs.c.organization_id, _runs.c.simulation_results
        ).where(_runs.c.simulation_results.is_not(None))
        if last is not None:
            q = q.where(_runs.c.id > last)
        rows = bind.execute(q.order_by(_runs.c.id).limit(_BATCH)).fetchall()
        if not rows:
            break
        for run_id, org_id, payload in rows:
            last = run_id
            if not isinstance(payload, dict):
                continue
            summary, arrays = _split(payload)
            if arrays:
                bind.execute(
                    _samples.insert().values(
                        run_id=run_id,
                        organization_id=org_id,
                        arrays=arrays,
                        created_at=sa.func.now(),
                    )
                )
            bind.execute(
                _runs.update()
                .where(_runs.c.id == run_id)
                .values(simulation_results=summary)
            )

    # Seed one system_state row per existing org (belt-and-suspenders; a later
    # task self-seeds via upsert, so this only covers pre-existing orgs).
    org_ids = bind.execute(sa.select(_organizations.c.id)).scalars().all()
    for org_id in org_ids:
        bind.execute(
            _system_state.insert().values(
                id=uuid.uuid4(),
                organization_id=org_id,
                last_retention_sweep_at=None,
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()

    # Re-merge each run's split arrays back into its simulation_results before
    # dropping run_samples, so simulation_results is whole again after the
    # downgrade. Keyset over run_samples (the only rows that were split).
    last = None
    while True:
        q = sa.select(_samples.c.run_id, _samples.c.arrays)
        if last is not None:
            q = q.where(_samples.c.run_id > last)
        rows = bind.execute(q.order_by(_samples.c.run_id).limit(_BATCH)).fetchall()
        if not rows:
            break
        for run_id, arrays in rows:
            last = run_id
            summary = bind.execute(
                sa.select(_runs.c.simulation_results).where(_runs.c.id == run_id)
            ).scalar_one_or_none()
            if not isinstance(summary, dict):
                continue
            merged = _merge(summary, arrays)
            bind.execute(
                _runs.update()
                .where(_runs.c.id == run_id)
                .values(simulation_results=merged)
            )

    op.drop_index(op.f("ix_system_state_organization_id"), table_name="system_state")
    op.drop_table("system_state")
    op.drop_index(op.f("ix_run_samples_organization_id"), table_name="run_samples")
    op.drop_table("run_samples")
