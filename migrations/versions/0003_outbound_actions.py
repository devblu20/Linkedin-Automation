"""Add qualification signals and auditable outbound actions.

Revision ID: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("company_size_min", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("company_size_max", sa.Integer(), nullable=True))
    op.add_column("leads", sa.Column("self_employed", sa.Boolean(), nullable=True))
    op.create_table(
        "connection_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_connection_requests_lead_id", "connection_requests", ["lead_id"])
    op.create_index("ix_connection_requests_run_id", "connection_requests", ["run_id"])
    op.create_index("ix_connection_requests_status", "connection_requests", ["status"])
    op.create_index("ix_connection_requests_requested_at", "connection_requests", ["requested_at"])
    op.create_table(
        "messages_sent",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_messages_sent_lead_id", "messages_sent", ["lead_id"])
    op.create_index("ix_messages_sent_run_id", "messages_sent", ["run_id"])
    op.create_index("ix_messages_sent_status", "messages_sent", ["status"])
    op.create_index("ix_messages_sent_sent_at", "messages_sent", ["sent_at"])


def downgrade() -> None:
    op.drop_table("messages_sent")
    op.drop_table("connection_requests")
    op.drop_column("leads", "self_employed")
    op.drop_column("leads", "company_size_max")
    op.drop_column("leads", "company_size_min")
