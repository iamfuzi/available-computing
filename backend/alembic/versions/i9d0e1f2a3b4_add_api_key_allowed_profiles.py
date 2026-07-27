"""add API key allowed_profiles for routing profile authorization

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-07-24 00:00:01.000000

Adds an ``allowed_profiles`` column to the ``apikey`` table. The column stores
a JSON array of routing-profile names the key is authorized to use
(None/empty = all profiles, the personal-deployment default).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "h8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("apikey", schema=None) as batch_op:
        batch_op.add_column(sa.Column("allowed_profiles", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("apikey", schema=None) as batch_op:
        batch_op.drop_column("allowed_profiles")
