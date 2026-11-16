"""Add AI triage requests, suggestions and human decisions.

Revision ID: 0008
Revises: 0007

- triage_requests: one row per triage request, written before the provider is called. It is the
  budget reservation (reserved_microusd) and records who asked, for which incident, which
  provider, model and prompt version, the incident's latest audit event at that moment (for the
  staleness check), the SHA-256 of the exact input and the duplicate candidates sent. The input
  text itself is never stored.
- triage_suggestions: the result of a request (outcome, validated fields only, token usage,
  actual cost, latency). Raw model output is never stored.
- triage_decisions: a person's accept or reject decision; an accepted severity is applied
  through the normal audited update path and resulting_event_id links that audit event here.

All three are append-only (trigger), like the other audit tables; the same limit applies (the
table owner can disable the trigger; ISSUE-12). Also adds a GIN full-text index on incident
title and description, used to find duplicate candidates.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

triage_outcome = sa.Enum("valid", "invalid_output", "refused", "timeout", "provider_error", "abandoned", name="triage_outcome")
triage_decision_kind = sa.Enum("accepted", "accepted_with_edits", "rejected", name="triage_decision_kind")
# Existing type from migration 0001; not created or dropped here.
incident_severity = postgresql.ENUM("low", "medium", "high", "critical", name="incident_severity", create_type=False)

TABLES = ("triage_requests", "triage_suggestions", "triage_decisions")
SEARCH_EXPRESSION = "to_tsvector('english', title || ' ' || description)"


def upgrade():
    op.create_table(
        "triage_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("basis_event_id", sa.Integer(), sa.ForeignKey("incident_events.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("candidate_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reserved_microusd", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("reserved_microusd >= 0", name="ck_triage_requests_reserved"),
    )
    op.create_index("ix_triage_requests_requested_by_created_at", "triage_requests", ["requested_by", "created_at"])
    op.create_index("ix_triage_requests_incident_id_created_at", "triage_requests", ["incident_id", "created_at"])
    op.create_index("ix_triage_requests_created_at", "triage_requests", ["created_at"])

    op.create_table(
        "triage_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("triage_requests.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("outcome", triage_outcome, nullable=False),
        sa.Column("error_category", sa.String(48), nullable=True),
        sa.Column("severity", incident_severity, nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("next_steps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duplicate_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_microusd", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("cost_microusd >= 0", name="ck_triage_suggestions_cost"),
        sa.CheckConstraint(
            "(outcome = 'valid') = (severity IS NOT NULL AND rationale IS NOT NULL AND next_steps IS NOT NULL AND duplicate_ids IS NOT NULL)",
            name="ck_triage_suggestions_valid_fields",
        ),
        sa.CheckConstraint("rationale IS NULL OR char_length(rationale) BETWEEN 1 AND 600", name="ck_triage_suggestions_rationale"),
    )

    op.create_table(
        "triage_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("suggestion_id", sa.Integer(), sa.ForeignKey("triage_suggestions.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decision", triage_decision_kind, nullable=False),
        sa.Column("applied_severity", incident_severity, nullable=True),
        sa.Column("resulting_event_id", sa.Integer(), sa.ForeignKey("incident_events.id", ondelete="RESTRICT"), nullable=True, unique=True),
        sa.Column("note_comment_id", sa.Integer(), sa.ForeignKey("incident_comments.id", ondelete="RESTRICT"), nullable=True, unique=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("reason IS NULL OR char_length(reason) BETWEEN 1 AND 500", name="ck_triage_decisions_reason"),
        sa.CheckConstraint(
            "decision <> 'rejected' OR (applied_severity IS NULL AND resulting_event_id IS NULL AND note_comment_id IS NULL)",
            name="ck_triage_decisions_rejected_changes_nothing",
        ),
    )

    op.execute(
        """
        CREATE FUNCTION triage_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    for table in TABLES:
        op.execute(f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION triage_append_only()")
        op.execute(f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION triage_append_only()")

    # Stored generated column: PostgreSQL computes it on every insert and update (rewrites the table
    # once on upgrade). Ranking reads the stored vector instead of recomputing it per row.
    op.add_column("incidents", sa.Column("search_vector", postgresql.TSVECTOR(), sa.Computed(SEARCH_EXPRESSION, persisted=True)))
    op.create_index("ix_incidents_search", "incidents", ["search_vector"], postgresql_using="gin")


def downgrade():
    # Downgrading deliberately discards every triage request, suggestion and decision.
    op.drop_index("ix_incidents_search", table_name="incidents")
    op.drop_column("incidents", "search_vector")
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER {table}_no_update_delete ON {table}")
    op.execute("DROP FUNCTION triage_append_only()")
    op.drop_table("triage_decisions")
    op.drop_table("triage_suggestions")
    op.drop_index("ix_triage_requests_created_at", table_name="triage_requests")
    op.drop_index("ix_triage_requests_incident_id_created_at", table_name="triage_requests")
    op.drop_index("ix_triage_requests_requested_by_created_at", table_name="triage_requests")
    op.drop_table("triage_requests")
    triage_decision_kind.drop(op.get_bind(), checkfirst=True)
    triage_outcome.drop(op.get_bind(), checkfirst=True)
