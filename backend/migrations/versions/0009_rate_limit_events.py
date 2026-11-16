"""Add shared rate-limit events (login failures and public-demo write limits).

Revision ID: 0009
Revises: 0008

One row per counted event: a bucket (for example login_email or write_comment), the SHA-256 of the
key (an email address or client IP address; never stored in clear) and the time. Counting rows in a
sliding window gives limits that are shared by every process and survive restarts. Old rows are
pruned by the maintenance task.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rate_limit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("bucket", sa.String(32), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("char_length(key_hash) = 64", name="ck_rate_limit_events_key_hash"),
    )
    op.create_index("ix_rate_limit_events_bucket_key_hash_created_at", "rate_limit_events", ["bucket", "key_hash", "created_at"])
    op.create_index("ix_rate_limit_events_created_at", "rate_limit_events", ["created_at"])


def downgrade():
    op.drop_index("ix_rate_limit_events_created_at", table_name="rate_limit_events")
    op.drop_index("ix_rate_limit_events_bucket_key_hash_created_at", table_name="rate_limit_events")
    op.drop_table("rate_limit_events")
