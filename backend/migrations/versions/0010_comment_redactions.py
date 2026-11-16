"""Add append-only comment redactions (ISSUE-18).

Revision ID: 0010
Revises: 0009

An administrator can redact a comment or internal note. The comment row itself never changes (it
stays append-only as the original evidence); a separate append-only redaction row records who
redacted it, when and why, and every API reader then withholds the body. One redaction per comment.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

FORBIDDEN = r"\u0001-\u0008\u000b-\u001f\u007f-\u009f"


def upgrade():
    op.create_table(
        "comment_redactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("comment_id", sa.Integer(), sa.ForeignKey("incident_comments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("redacted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("comment_id", name="uq_comment_redactions_comment_id"),
        sa.CheckConstraint("char_length(reason) BETWEEN 1 AND 500", name="ck_comment_redactions_reason_length"),
        sa.CheckConstraint(f"reason !~ '[{FORBIDDEN}]'", name="ck_comment_redactions_reason_chars"),
    )
    op.create_index("ix_comment_redactions_redacted_by", "comment_redactions", ["redacted_by"])
    op.execute(
        """
        CREATE FUNCTION comment_redactions_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'comment_redactions is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER comment_redactions_no_update_delete BEFORE UPDATE OR DELETE ON comment_redactions "
        "FOR EACH ROW EXECUTE FUNCTION comment_redactions_immutable()"
    )
    op.execute(
        "CREATE TRIGGER comment_redactions_no_truncate BEFORE TRUNCATE ON comment_redactions "
        "FOR EACH STATEMENT EXECUTE FUNCTION comment_redactions_immutable()"
    )


def downgrade():
    # Downgrading discards the redaction records; the comments themselves are untouched.
    op.execute("DROP TRIGGER comment_redactions_no_truncate ON comment_redactions")
    op.execute("DROP TRIGGER comment_redactions_no_update_delete ON comment_redactions")
    op.execute("DROP FUNCTION comment_redactions_immutable()")
    op.drop_index("ix_comment_redactions_redacted_by", table_name="comment_redactions")
    op.drop_table("comment_redactions")
