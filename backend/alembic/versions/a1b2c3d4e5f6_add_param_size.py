"""add param_size to model

Revision ID: a1b2c3d4e5f6
Revises: 7526ef5a88ed
Create Date: 2026-06-23 10:00:00.000000

Adds ``param_size`` (parameter count in billions) to the model table. It is
populated during discovery by parsing the model id, with a whitelist override
for closed-source ids that carry no numeric size marker. The value drives the
``auto:smart`` router, which prefers larger (generally more capable) models.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '7526ef5a88ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('model', schema=None) as batch_op:
        # Nullable: existing rows (and any model whose size can't be parsed)
        # simply have no param_size and sort last under auto:smart.
        batch_op.add_column(sa.Column('param_size', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('model', schema=None) as batch_op:
        batch_op.drop_column('param_size')
