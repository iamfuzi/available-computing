"""add API key routing policy

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-20 00:00:01.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("apikey", schema=None) as batch_op:
        batch_op.add_column(sa.Column("provider_whitelist", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("provider_blacklist", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_rpm", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_rpd", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("default_prefer", sa.String(), nullable=False, server_default="latency")
        )
        batch_op.add_column(sa.Column("default_min_context", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("apikey", schema=None) as batch_op:
        batch_op.drop_column("default_min_context")
        batch_op.drop_column("default_prefer")
        batch_op.drop_column("rate_limit_rpd")
        batch_op.drop_column("rate_limit_rpm")
        batch_op.drop_column("provider_blacklist")
        batch_op.drop_column("provider_whitelist")
