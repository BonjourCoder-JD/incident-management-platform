"""Add users, login sessions and incident ownership.

Revision ID: 0002
Revises: 0001

Legacy data strategy:
- Every existing incident is kept. Its reporter_id is NULL ("legacy"), so it is
  visible to responders and admins only, never to reporters.
- assignee_id becomes a foreign key to users. Before this revision there were no
  users, so any existing assignee_id referred to nobody; such values are set to
  NULL. This step is not reversed by downgrade.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

user_role = sa.Enum("reporter", "responder", "admin", name="user_role")


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", user_role, nullable=False, server_default="reporter"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email"),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.add_column("incidents", sa.Column("reporter_id", sa.Integer(), nullable=True))
    op.create_foreign_key("incidents_reporter_id_fkey", "incidents", "users", ["reporter_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_incidents_reporter_id", "incidents", ["reporter_id"])

    # Legacy assignee IDs cannot reference users (the table was empty until now).
    op.execute("UPDATE incidents SET assignee_id = NULL WHERE assignee_id IS NOT NULL")
    op.create_foreign_key("incidents_assignee_id_fkey", "incidents", "users", ["assignee_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_incidents_assignee_id", "incidents", ["assignee_id"])


def downgrade():
    op.drop_index("ix_incidents_assignee_id", table_name="incidents")
    op.drop_constraint("incidents_assignee_id_fkey", "incidents", type_="foreignkey")
    op.drop_index("ix_incidents_reporter_id", table_name="incidents")
    op.drop_constraint("incidents_reporter_id_fkey", "incidents", type_="foreignkey")
    op.drop_column("incidents", "reporter_id")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("users")
    user_role.drop(op.get_bind(), checkfirst=True)
