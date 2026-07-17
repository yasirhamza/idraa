"""phase 1.4 simulation runs

Revision ID: 3e5d550fcade
Revises: 1a3794c327d4
Create Date: 2026-04-27 20:54:13.548694
"""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op


revision = "3e5d550fcade"
down_revision = "1a3794c327d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. scenario_controls join table
    op.create_table(
        "scenario_controls",
        sa.Column("scenario_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("control_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["control_id"], ["controls.id"], ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("scenario_id", "control_id"),
    )

    # 2. scenarios.mc_iterations column
    op.add_column(
        "scenarios",
        sa.Column(
            "mc_iterations",
            sa.Integer(),
            server_default=sa.text("10000"),
            nullable=False,
        ),
    )

    # 3. risk_analysis_runs table
    op.create_table(
        "risk_analysis_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scenario_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("run_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("mc_iterations", sa.Integer(), nullable=False),
        sa.Column("inputs_hash", sa.String(64), nullable=False),
        sa.Column("controls_snapshot", sa.JSON(), nullable=False),
        sa.Column("control_ids_used", sa.JSON(), nullable=False),
        sa.Column("aggregate_scenario_ids", sa.JSON(), nullable=True),
        sa.Column("simulation_results", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_risk_analysis_runs_scenario_id_created_at",
        "risk_analysis_runs",
        ["scenario_id", "created_at"],
    )
    op.create_index(
        "ix_risk_analysis_runs_org_status",
        "risk_analysis_runs",
        ["organization_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_risk_analysis_runs_org_status", table_name="risk_analysis_runs")
    op.drop_index(
        "ix_risk_analysis_runs_scenario_id_created_at",
        table_name="risk_analysis_runs",
    )
    op.drop_table("risk_analysis_runs")
    op.drop_column("scenarios", "mc_iterations")
    op.drop_table("scenario_controls")
