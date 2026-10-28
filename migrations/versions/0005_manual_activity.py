"""Add append-only manual activity records without modifying existing lead data."""

from alembic import op

from linkedin_automation.infrastructure.persistence.repository import ManualActivityRow

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    ManualActivityRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    ManualActivityRow.__table__.drop(op.get_bind(), checkfirst=True)
