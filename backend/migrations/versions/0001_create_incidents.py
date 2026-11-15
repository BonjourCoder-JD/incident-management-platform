"""Create incidents table.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

severity = sa.Enum("low", "medium", "high", "critical", name="incident_severity")
status = sa.Enum("open", "investigating", "resolved", "closed", name="incident_status")

def upgrade():
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", severity, nullable=False),
        sa.Column("status", status, nullable=False, server_default="open"),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_incidents_status_severity", "incidents", ["status", "severity"])

def downgrade():
    op.drop_index("ix_incidents_status_severity", table_name="incidents")
    op.drop_table("incidents")
    status.drop(op.get_bind(), checkfirst=True)
    severity.drop(op.get_bind(), checkfirst=True)
