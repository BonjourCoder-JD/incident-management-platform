"""Add the append-only incident audit trail.

Revision ID: 0003
Revises: 0002

No historical events are backfilled: changes made before this revision have no
trustworthy actor or timestamp, so history starts when this migration runs.

A trigger rejects UPDATE, DELETE and TRUNCATE on incident_events. Limits: the table
owner or a superuser can still disable or drop the trigger, and
session_replication_role = replica skips row triggers. Production should run the app
as a role that does not own the table (Milestone 6).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

event_action = sa.Enum("created", "updated", "status_changed", "assignment_changed", name="incident_event_action")


def upgrade():
    op.create_table(
        "incident_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", event_action, nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_incident_events_incident_id_id", "incident_events", ["incident_id", "id"])
    op.create_index("ix_incident_events_actor_id", "incident_events", ["actor_id"])

    op.execute(
        """
        CREATE FUNCTION incident_events_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'incident_events is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER incident_events_no_update_delete BEFORE UPDATE OR DELETE ON incident_events "
        "FOR EACH ROW EXECUTE FUNCTION incident_events_immutable()"
    )
    op.execute(
        "CREATE TRIGGER incident_events_no_truncate BEFORE TRUNCATE ON incident_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION incident_events_immutable()"
    )


def downgrade():
    # Downgrading deliberately discards the audit history (DROP TABLE is not blocked by the triggers).
    op.execute("DROP TRIGGER incident_events_no_truncate ON incident_events")
    op.execute("DROP TRIGGER incident_events_no_update_delete ON incident_events")
    op.execute("DROP FUNCTION incident_events_immutable()")
    op.drop_index("ix_incident_events_actor_id", table_name="incident_events")
    op.drop_index("ix_incident_events_incident_id_id", table_name="incident_events")
    op.drop_table("incident_events")
    event_action.drop(op.get_bind(), checkfirst=True)
