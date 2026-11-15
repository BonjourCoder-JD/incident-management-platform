"""Add the append-only record of CSV exports.

Revision ID: 0005
Revises: 0004

One row per export request: who, which filters, how many rows matched, the limit in force,
when, and whether the export was initiated (streaming began) or rejected (over the limit).
A completed download is never recorded, because the server cannot confirm it.

Like incident_events, a trigger rejects UPDATE, DELETE and TRUNCATE. The same limits apply:
the table owner or a superuser can disable or drop the trigger (see ISSUE-12).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

export_outcome = sa.Enum("initiated", "rejected", name="export_outcome")


def upgrade():
    op.create_table(
        "incident_exports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("outcome", export_outcome, nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("row_limit", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_incident_exports_actor_id_created_at", "incident_exports", ["actor_id", "created_at"])
    op.execute(
        """
        CREATE FUNCTION incident_exports_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'incident_exports is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER incident_exports_no_update_delete BEFORE UPDATE OR DELETE ON incident_exports "
        "FOR EACH ROW EXECUTE FUNCTION incident_exports_immutable()"
    )
    op.execute(
        "CREATE TRIGGER incident_exports_no_truncate BEFORE TRUNCATE ON incident_exports "
        "FOR EACH STATEMENT EXECUTE FUNCTION incident_exports_immutable()"
    )


def downgrade():
    # Downgrading deliberately discards the export records.
    op.execute("DROP TRIGGER incident_exports_no_truncate ON incident_exports")
    op.execute("DROP TRIGGER incident_exports_no_update_delete ON incident_exports")
    op.execute("DROP FUNCTION incident_exports_immutable()")
    op.drop_index("ix_incident_exports_actor_id_created_at", table_name="incident_exports")
    op.drop_table("incident_exports")
    export_outcome.drop(op.get_bind(), checkfirst=True)
