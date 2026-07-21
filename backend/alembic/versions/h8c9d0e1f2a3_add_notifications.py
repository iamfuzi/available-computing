"""add persistent notifications

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-07-21 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "g7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("notification"):
        return
    op.create_table(
        "notification",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False, server_default="info"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("action_path", sa.String(), nullable=True),
        sa.Column("payload_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(), nullable=False, server_default="unread"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_notification_dedupe_key", "notification", ["dedupe_key"])
    op.create_index("ix_notification_category", "notification", ["category"])
    op.create_index("ix_notification_severity", "notification", ["severity"])
    op.create_index("ix_notification_status", "notification", ["status"])
    op.create_index("ix_notification_updated_at", "notification", ["updated_at"])
    op.create_index("ix_notification_resolved_at", "notification", ["resolved_at"])


def downgrade() -> None:
    op.drop_table("notification")
