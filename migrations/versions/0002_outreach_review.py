"""Add human-reviewed firm qualification and outreach records.

Revision ID: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outreach_records",
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id"), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("firm_type", sa.String(255), nullable=False, server_default=""),
        sa.Column("funds_gbp", sa.Integer(), nullable=True),
        sa.Column("fund_evidence_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("fund_evidence_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("connection_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("follow_up_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outreach_run_id", "outreach_records", ["run_id"])
    op.create_index("ix_outreach_status", "outreach_records", ["status"])


def downgrade() -> None:
    op.drop_table("outreach_records")
