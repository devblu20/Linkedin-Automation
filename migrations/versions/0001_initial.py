"""Create research runs, observations, and leads.

Revision ID: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("search_name", sa.String(255), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checkpoint_page", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report_path", sa.Text(), nullable=True),
        sa.Column("drive_file_id", sa.String(255), nullable=True),
        sa.Column("drive_url", sa.Text(), nullable=True),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_runs_hash", "research_runs", ["definition_hash"])
    op.create_index("ix_runs_status", "research_runs", ["status"])
    op.create_table(
        "observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("candidate_json", sa.Text(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_observations_run_id", "observations", ["run_id"])
    op.create_index("ix_observations_collected_at", "observations", ["collected_at"])
    op.create_table(
        "leads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("profile_url", sa.Text(), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("current_title", sa.String(255), nullable=False),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=False),
        sa.Column("industry", sa.String(255), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("source_search", sa.Text(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.UniqueConstraint("run_id", "profile_url", name="uq_run_profile"),
    )
    op.create_index("ix_leads_run_id", "leads", ["run_id"])


def downgrade() -> None:
    op.drop_table("leads")
    op.drop_table("observations")
    op.drop_table("research_runs")
