"""add candidate provider pool

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-21 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Application startup calls SQLModel.create_all before Alembic so a newly
    # introduced table may already exist even though the revision has not been
    # stamped yet. Keep the migration valid for both that adoption path and a
    # conventional Alembic-only upgrade.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("candidateprovider"):
        op.create_table(
            "candidateprovider",
            sa.Column("provider_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("homepage_url", sa.String(), nullable=False),
            sa.Column("base_url", sa.String(), nullable=True),
            sa.Column("summary", sa.String(), nullable=False, server_default=""),
            sa.Column("compatibility", sa.String(), nullable=False, server_default="unknown"),
            sa.Column("model_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("models_json", sa.String(), nullable=False, server_default="[]"),
            sa.Column("sources_json", sa.String(), nullable=False, server_default="[]"),
            sa.Column("evidence_json", sa.String(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("yaml_draft", sa.String(), nullable=True),
            sa.Column("is_present", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_changed_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("provider_id"),
        )
        op.create_index("ix_candidateprovider_status", "candidateprovider", ["status"])
        op.create_index("ix_candidateprovider_compatibility", "candidateprovider", ["compatibility"])
        op.create_index("ix_candidateprovider_is_present", "candidateprovider", ["is_present"])
        op.create_index("ix_candidateprovider_last_seen_at", "candidateprovider", ["last_seen_at"])

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("candidatesourcestate"):
        op.create_table(
            "candidatesourcestate",
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("url", sa.String(), nullable=False),
            sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("last_success_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("last_candidate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("needs_attention", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.PrimaryKeyConstraint("source_id"),
        )
        op.create_index("ix_candidatesourcestate_needs_attention", "candidatesourcestate", ["needs_attention"])


def downgrade() -> None:
    op.drop_index("ix_candidatesourcestate_needs_attention", table_name="candidatesourcestate")
    op.drop_table("candidatesourcestate")
    op.drop_index("ix_candidateprovider_last_seen_at", table_name="candidateprovider")
    op.drop_index("ix_candidateprovider_is_present", table_name="candidateprovider")
    op.drop_index("ix_candidateprovider_compatibility", table_name="candidateprovider")
    op.drop_index("ix_candidateprovider_status", table_name="candidateprovider")
    op.drop_table("candidateprovider")
