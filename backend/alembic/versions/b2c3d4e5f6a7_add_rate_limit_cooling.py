"""add rate-limit cooling fields to model

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-30 00:00:00.000000

Stores upstream 429 state so routing can skip cooled-down models instead of
hammering the same free-tier endpoint and returning repeated rate-limit errors.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('model', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_success_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('rate_limited_until', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('last_429_at', sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column('consecutive_429', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('model', schema=None) as batch_op:
        batch_op.drop_column('consecutive_429')
        batch_op.drop_column('last_429_at')
        batch_op.drop_column('rate_limited_until')
        batch_op.drop_column('last_success_at')
