"""Store visible LinkedIn profile details.

Revision ID: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("about", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "leads", sa.Column("experience_json", sa.Text(), nullable=False, server_default="[]")
    )
    op.add_column(
        "leads", sa.Column("education_json", sa.Text(), nullable=False, server_default="[]")
    )
    op.add_column("leads", sa.Column("skills_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column(
        "leads", sa.Column("connections", sa.String(64), nullable=False, server_default="")
    )
    op.add_column("leads", sa.Column("followers", sa.String(64), nullable=False, server_default=""))
    op.add_column(
        "leads", sa.Column("profile_snapshot", sa.Text(), nullable=False, server_default="")
    )


def downgrade() -> None:
    for column in (
        "profile_snapshot",
        "followers",
        "connections",
        "skills_json",
        "education_json",
        "experience_json",
        "about",
    ):
        op.drop_column("leads", column)
