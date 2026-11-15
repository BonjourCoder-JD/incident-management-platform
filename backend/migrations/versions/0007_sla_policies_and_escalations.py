"""Add SLA policy versions, per-severity targets and automatic escalation records.

Revision ID: 0007
Revises: 0006

- sla_policy_versions: immutable policy versions. A version applies to SLA cycles that start at
  or after its created_at (ties broken by id). Version 1 is seeded here (source 'default'), so
  cycles that started before this migration are not tracked (no flood of escalations).
- sla_policy_targets: response and resolution minutes for each severity of a version.
- incident_escalations: one row per incident, cycle and target, written only by the SLA worker
  (triggered_by is always 'system'); the row is the escalation's audit record.

- incident_sla_cycles: the stored SLA read model, one row per cycle of every audited incident,
  maintained by the application with each incident change (app/sla_store.py). incident_events stays
  the source of truth: app/sla.py derives the same values from it, and tests compare the two.
  Existing incidents are backfilled from their audit events (BACKFILL_SQL). Cycles that started
  before policy version 1 get no policy version, so they are not tracked.

Nothing in incidents or incident_events changes, so this migration is cleanly reversible.

All three tables are append-only: a trigger rejects UPDATE, DELETE and TRUNCATE, with the same
limit as the other append-only tables (the table owner can disable it; ISSUE-12).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

sla_target = sa.Enum("response", "resolution", name="sla_target")
sla_policy_source = sa.Enum("default", "admin", name="sla_policy_source")
# Existing type from migration 0001; not created or dropped here.
incident_severity = postgresql.ENUM("low", "medium", "high", "critical", name="incident_severity", create_type=False)

TABLES = ("sla_policy_versions", "sla_policy_targets", "incident_escalations")

RANK = "CASE {0} WHEN 'low' THEN 1 WHEN 'medium' THEN 2 WHEN 'high' THEN 3 WHEN 'critical' THEN 4 END"

# Every cycle of every audited incident, from its audit events; the same definitions as app/sla.py
# (events sequenced by id; "latest so far" as a running max of id * 8 + value).
BACKFILL_SQL = f"""
WITH ev AS (
    SELECT e.incident_id, e.id, e.created_at,
           coalesce((e.changes -> 'status' ->> 'from') IN ('resolved', 'closed')
                    AND (e.changes -> 'status' ->> 'to') IN ('open', 'investigating'), false) AS is_reopen,
           coalesce((e.changes -> 'status' ->> 'to') IN ('resolved', 'closed'), false) AS is_resolution,
           coalesce(((e.changes -> 'status' ->> 'from') = 'open' AND (e.changes -> 'status' ->> 'to') <> 'open')
                    OR (e.changes -> 'assignee_id' ->> 'to') IS NOT NULL, false) AS is_ack,
           e.action = 'created' AS is_created,
           e.id * 8 + {RANK.format("e.changes -> 'severity' ->> 'to'")} AS sev_key
    FROM incident_events e
    WHERE e.action = 'created' OR e.changes ?| ARRAY['status', 'assignee_id', 'severity']
),
run AS (
    SELECT incident_id, id, created_at, is_resolution, is_ack,
           sum(CASE WHEN is_reopen THEN 1 ELSE 0 END) OVER w AS cycle,
           (max(sev_key) OVER w) % 8 AS sev_at,
           max(CASE WHEN is_reopen OR is_created THEN id END) OVER w AS start_id,
           max(CASE WHEN is_resolution THEN id END) OVER w AS last_res_id,
           max(CASE WHEN is_ack THEN id END) OVER w AS last_ack_id,
           bool_or(is_created) OVER (PARTITION BY incident_id) AS audited
    FROM ev
    WINDOW w AS (PARTITION BY incident_id ORDER BY id)
),
cyc AS (
    SELECT incident_id, cycle,
           max(created_at) FILTER (WHERE id = start_id) AS started_at,
           (array_agg(created_at ORDER BY id) FILTER (WHERE is_resolution))[1] AS resolved_at,
           (array_agg(created_at ORDER BY id) FILTER (WHERE is_ack))[1] AS acknowledged_at,
           coalesce(max(sev_at) FILTER (WHERE last_res_id IS NULL OR last_res_id < start_id),
                    max(sev_at) FILTER (WHERE id = start_id)) AS res_sev,
           coalesce(max(sev_at) FILTER (WHERE last_ack_id IS NULL), max(sev_at) FILTER (WHERE id = start_id)) AS ack_sev
    FROM run
    WHERE audited
    GROUP BY incident_id, cycle
),
ranked AS (
    SELECT c.*, v.id AS version_id,
           coalesce(nullif(c.res_sev, 0), {RANK.format("i.severity::text")}) AS res_rank,
           coalesce(nullif(c.ack_sev, 0), {RANK.format("i.severity::text")}) AS ack_rank
    FROM cyc c
    JOIN incidents i ON i.id = c.incident_id
    LEFT JOIN LATERAL (
        SELECT id FROM sla_policy_versions WHERE created_at <= c.started_at ORDER BY created_at DESC, id DESC LIMIT 1
    ) v ON true
)
INSERT INTO incident_sla_cycles (
    incident_id, cycle, started_at, policy_version_id,
    response_rank, response_minutes, response_due_at, response_warning_at, acknowledged_at,
    resolution_rank, resolution_minutes, resolution_due_at, resolution_warning_at, resolved_at
)
SELECT r.incident_id, r.cycle, r.started_at, r.version_id,
       CASE WHEN r.cycle = 0 THEN r.ack_rank END,
       CASE WHEN r.cycle = 0 THEN ack.response_minutes END,
       CASE WHEN r.cycle = 0 THEN r.started_at + make_interval(mins => ack.response_minutes) END,
       CASE WHEN r.cycle = 0 THEN r.started_at + make_interval(secs => ack.response_minutes * 45) END,
       CASE WHEN r.cycle = 0 THEN r.acknowledged_at END,
       r.res_rank, res.resolution_minutes,
       r.started_at + make_interval(mins => res.resolution_minutes),
       r.started_at + make_interval(secs => res.resolution_minutes * 45),
       r.resolved_at
FROM ranked r
LEFT JOIN sla_policy_targets res ON res.version_id = r.version_id AND {RANK.format("res.severity::text")} = r.res_rank
LEFT JOIN sla_policy_targets ack ON ack.version_id = r.version_id AND {RANK.format("ack.severity::text")} = r.ack_rank
"""

CYCLE_GUARD = """
CREATE FUNCTION incident_sla_cycles_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'incident_sla_cycles keeps every cycle: % is not allowed', TG_OP USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.resolved_at IS NOT NULL THEN
        RAISE EXCEPTION 'a completed SLA cycle is final' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.incident_id <> OLD.incident_id OR NEW.cycle <> OLD.cycle OR NEW.started_at <> OLD.started_at
       OR NEW.policy_version_id IS DISTINCT FROM OLD.policy_version_id THEN
        RAISE EXCEPTION 'an SLA cycle keeps its start and policy version' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.acknowledged_at IS NOT NULL AND (NEW.acknowledged_at IS DISTINCT FROM OLD.acknowledged_at
       OR NEW.response_due_at IS DISTINCT FROM OLD.response_due_at OR NEW.response_rank IS DISTINCT FROM OLD.response_rank) THEN
        RAISE EXCEPTION 'an acknowledged response target is final' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.response_due_at > OLD.response_due_at OR NEW.resolution_due_at > OLD.resolution_due_at
       OR NEW.response_rank < OLD.response_rank OR NEW.resolution_rank < OLD.resolution_rank THEN
        RAISE EXCEPTION 'SLA targets can only become stricter' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$
"""

# Approved defaults (D1), in minutes: (response, resolution).
DEFAULT_TARGETS = {
    "critical": (15, 4 * 60),
    "high": (60, 24 * 60),
    "medium": (4 * 60, 3 * 24 * 60),
    "low": (24 * 60, 7 * 24 * 60),
}


def upgrade():
    op.create_table(
        "sla_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sla_policy_source, nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("(source = 'admin') = (created_by IS NOT NULL)", name="ck_sla_policy_versions_author"),
        sa.CheckConstraint("char_length(reason) BETWEEN 1 AND 500", name="ck_sla_policy_versions_reason"),
    )
    op.create_index("ix_sla_policy_versions_created_at_id", "sla_policy_versions", ["created_at", "id"])

    op.create_table(
        "sla_policy_targets",
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("sla_policy_versions.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("severity", incident_severity, primary_key=True),
        sa.Column("response_minutes", sa.Integer(), nullable=False),
        sa.Column("resolution_minutes", sa.Integer(), nullable=False),
        sa.CheckConstraint("response_minutes BETWEEN 1 AND 43200", name="ck_sla_policy_targets_response"),
        sa.CheckConstraint("resolution_minutes BETWEEN 1 AND 525600", name="ck_sla_policy_targets_resolution"),
        sa.CheckConstraint("response_minutes <= resolution_minutes", name="ck_sla_policy_targets_order"),
    )

    op.create_table(
        "incident_escalations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("cycle", sa.Integer(), nullable=False),
        sa.Column("target", sla_target, nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), sa.ForeignKey("sla_policy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("triggered_by", sa.String(16), server_default="system", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("incident_id", "cycle", "target", name="uq_incident_escalations_incident_cycle_target"),
        sa.CheckConstraint("cycle >= 0", name="ck_incident_escalations_cycle"),
        sa.CheckConstraint("target <> 'response' OR cycle = 0", name="ck_incident_escalations_response_first_cycle"),
        sa.CheckConstraint("triggered_by = 'system'", name="ck_incident_escalations_system"),
    )
    op.create_index("ix_incident_escalations_created_at", "incident_escalations", ["created_at"])

    op.create_table(
        "incident_sla_cycles",
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("cycle", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), sa.ForeignKey("sla_policy_versions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("response_rank", sa.SmallInteger(), nullable=True),
        sa.Column("response_minutes", sa.Integer(), nullable=True),
        sa.Column("response_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_warning_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_rank", sa.SmallInteger(), nullable=False),
        sa.Column("resolution_minutes", sa.Integer(), nullable=True),
        sa.Column("resolution_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_warning_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_breached", sa.Boolean(), sa.Computed("acknowledged_at > response_due_at", persisted=True)),
        sa.Column("resolution_breached", sa.Boolean(), sa.Computed("resolved_at > resolution_due_at", persisted=True)),
        sa.CheckConstraint("cycle >= 0", name="ck_incident_sla_cycles_cycle"),
        sa.CheckConstraint(
            "cycle = 0 OR (response_rank IS NULL AND response_minutes IS NULL AND response_due_at IS NULL "
            "AND response_warning_at IS NULL AND acknowledged_at IS NULL)",
            name="ck_incident_sla_cycles_response_first_cycle",
        ),
        sa.CheckConstraint("(policy_version_id IS NULL) = (resolution_minutes IS NULL)", name="ck_incident_sla_cycles_tracked"),
        sa.CheckConstraint("resolution_rank BETWEEN 1 AND 4 AND (response_rank IS NULL OR response_rank BETWEEN 1 AND 4)", name="ck_incident_sla_cycles_rank"),
    )
    op.create_index("ix_incident_sla_cycles_open_resolution", "incident_sla_cycles", ["resolution_due_at"],
                    postgresql_where=sa.text("resolved_at IS NULL AND policy_version_id IS NOT NULL"))
    op.create_index("ix_incident_sla_cycles_open_response", "incident_sla_cycles", ["response_due_at"],
                    postgresql_where=sa.text("acknowledged_at IS NULL AND cycle = 0 AND policy_version_id IS NOT NULL"))
    op.create_index("ix_incident_sla_cycles_breached", "incident_sla_cycles", ["incident_id", "cycle"],
                    postgresql_where=sa.text("response_breached OR resolution_breached"))
    op.execute(CYCLE_GUARD)
    op.execute("CREATE TRIGGER incident_sla_cycles_guard BEFORE UPDATE OR DELETE ON incident_sla_cycles "
               "FOR EACH ROW EXECUTE FUNCTION incident_sla_cycles_guard()")
    op.execute("CREATE TRIGGER incident_sla_cycles_no_truncate BEFORE TRUNCATE ON incident_sla_cycles "
               "FOR EACH STATEMENT EXECUTE FUNCTION incident_sla_cycles_guard()")

    op.execute(
        """
        CREATE FUNCTION sla_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    for table in TABLES:
        op.execute(f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sla_append_only()")
        op.execute(f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION sla_append_only()")

    # Version 1: the approved default policy, effective from now.
    op.execute("INSERT INTO sla_policy_versions (source, reason) VALUES ('default', 'Default policy (migration 0007)')")
    values = ", ".join(
        f"((SELECT min(id) FROM sla_policy_versions), '{severity}', {response}, {resolution})"
        for severity, (response, resolution) in DEFAULT_TARGETS.items()
    )
    op.execute(f"INSERT INTO sla_policy_targets (version_id, severity, response_minutes, resolution_minutes) VALUES {values}")
    # Every existing cycle started before version 1, so none is tracked; the rows keep cycle numbering
    # and satisfaction times correct for any later reopen.
    op.execute(BACKFILL_SQL)


def downgrade():
    # Downgrading deliberately discards every policy version, stored SLA cycle and escalation record.
    op.execute("DROP TRIGGER incident_sla_cycles_no_truncate ON incident_sla_cycles")
    op.execute("DROP TRIGGER incident_sla_cycles_guard ON incident_sla_cycles")
    op.execute("DROP FUNCTION incident_sla_cycles_guard()")
    op.drop_index("ix_incident_sla_cycles_breached", table_name="incident_sla_cycles")
    op.drop_index("ix_incident_sla_cycles_open_response", table_name="incident_sla_cycles")
    op.drop_index("ix_incident_sla_cycles_open_resolution", table_name="incident_sla_cycles")
    op.drop_table("incident_sla_cycles")
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER {table}_no_update_delete ON {table}")
    op.execute("DROP FUNCTION sla_append_only()")
    op.drop_index("ix_incident_escalations_created_at", table_name="incident_escalations")
    op.drop_table("incident_escalations")
    op.drop_table("sla_policy_targets")
    op.drop_index("ix_sla_policy_versions_created_at_id", table_name="sla_policy_versions")
    op.drop_table("sla_policy_versions")
    sla_target.drop(op.get_bind(), checkfirst=True)
    sla_policy_source.drop(op.get_bind(), checkfirst=True)
