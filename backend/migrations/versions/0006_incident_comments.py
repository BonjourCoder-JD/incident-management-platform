"""Add append-only incident comments and internal notes.

Revision ID: 0006
Revises: 0005

One row per comment: incident, author (the signed-in user), visibility (`public` for the
reporter and staff, `internal` for responders and admins only), body and time. The row is the
comment's immutable audit record, so comment activity is not written to incident_events.

Body rules match app.schemas.clean_comment_body exactly (tests/test_comments.py checks both
accept and reject the same bodies):
- 1 to 5000 characters (code points);
- no C0 control characters other than tab and line feed (so no CR: line endings are normalised
  to LF), no DEL or C1 controls; NUL cannot be stored in PostgreSQL text at all;
- no leading or trailing blank characters from the same set the API trims, which also means a
  body can never consist only of blank characters.

Like incident_events and incident_exports, a trigger rejects UPDATE, DELETE and TRUNCATE, with
the same limit: the table owner or a superuser can disable or drop it (ISSUE-12). Redacting a
comment published by mistake is not possible through the application (ISSUE-18).
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

comment_visibility = sa.Enum("public", "internal", name="comment_visibility")

# PostgreSQL regular-expression bracket contents; \uXXXX and \t, \n are regex escapes.
BLANK = r" \t\n\u00a0\u1680\u2000-\u200b\u2028\u2029\u202f\u205f\u3000\ufeff"
FORBIDDEN = r"\u0001-\u0008\u000b-\u001f\u007f-\u009f"


def upgrade():
    op.create_table(
        "incident_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("visibility", comment_visibility, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 5000", name="ck_incident_comments_body_length"),
        sa.CheckConstraint(f"body !~ '[{FORBIDDEN}]'", name="ck_incident_comments_body_chars"),
        sa.CheckConstraint(f"body !~ '^[{BLANK}]' AND body !~ '[{BLANK}]$'", name="ck_incident_comments_body_trimmed"),
    )
    op.create_index("ix_incident_comments_incident_id_visibility_id", "incident_comments", ["incident_id", "visibility", "id"])
    op.create_index("ix_incident_comments_author_id", "incident_comments", ["author_id"])
    op.execute(
        """
        CREATE FUNCTION incident_comments_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'incident_comments is append-only: % is not allowed', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER incident_comments_no_update_delete BEFORE UPDATE OR DELETE ON incident_comments "
        "FOR EACH ROW EXECUTE FUNCTION incident_comments_immutable()"
    )
    op.execute(
        "CREATE TRIGGER incident_comments_no_truncate BEFORE TRUNCATE ON incident_comments "
        "FOR EACH STATEMENT EXECUTE FUNCTION incident_comments_immutable()"
    )


def downgrade():
    # Downgrading deliberately discards every comment and internal note.
    op.execute("DROP TRIGGER incident_comments_no_truncate ON incident_comments")
    op.execute("DROP TRIGGER incident_comments_no_update_delete ON incident_comments")
    op.execute("DROP FUNCTION incident_comments_immutable()")
    op.drop_index("ix_incident_comments_author_id", table_name="incident_comments")
    op.drop_index("ix_incident_comments_incident_id_visibility_id", table_name="incident_comments")
    op.drop_table("incident_comments")
    comment_visibility.drop(op.get_bind(), checkfirst=True)
