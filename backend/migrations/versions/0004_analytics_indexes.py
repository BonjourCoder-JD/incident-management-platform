"""Add indexes for incident analytics.

Revision ID: 0004
Revises: 0003

Chosen from EXPLAIN ANALYZE on ~50k incidents / ~225k audit events (docs/PROJECT_SPEC.md):
- ix_incidents_created_at serves every date-range filter and the created trend.
- ix_incident_events_first_resolution is a partial index on exactly the resolution predicate
  used by app/analytics.py; it serves both the in-range scan and the "resolved earlier?"
  anti-join. Its predicate must stay identical to analytics._resolution_event().
Not added, because the plans did not justify them: a (reporter_id, created_at) index (the
existing ix_incidents_reporter_id is kept) and a time-ordered resolution index.
Indexes only: no data changes, so downgrade is lossless.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

RESOLUTION_PREDICATE = "(changes -> 'status' ->> 'to') IN ('resolved', 'closed')"


def upgrade():
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"])
    op.create_index(
        "ix_incident_events_first_resolution",
        "incident_events",
        ["incident_id", "created_at"],
        postgresql_where=sa.text(RESOLUTION_PREDICATE),
    )


def downgrade():
    op.drop_index("ix_incident_events_first_resolution", table_name="incident_events")
    op.drop_index("ix_incidents_created_at", table_name="incidents")
