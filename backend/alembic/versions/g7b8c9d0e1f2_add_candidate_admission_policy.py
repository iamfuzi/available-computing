"""add candidate admission policy

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-21 12:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("candidateprovider", schema=None) as batch_op:
        batch_op.add_column(sa.Column("access_type", sa.String(), nullable=False, server_default="unknown"))
        batch_op.add_column(sa.Column("requires_card", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("admission_status", sa.String(), nullable=False, server_default="review_required"))
        batch_op.add_column(sa.Column("exclusion_reason", sa.String(), nullable=True))
        batch_op.create_index("ix_candidateprovider_access_type", ["access_type"], unique=False)
        batch_op.create_index("ix_candidateprovider_admission_status", ["admission_status"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("candidateprovider", schema=None) as batch_op:
        batch_op.drop_index("ix_candidateprovider_admission_status")
        batch_op.drop_index("ix_candidateprovider_access_type")
        batch_op.drop_column("exclusion_reason")
        batch_op.drop_column("admission_status")
        batch_op.drop_column("requires_card")
        batch_op.drop_column("access_type")
