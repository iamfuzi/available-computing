"""add consecutive_billing_failures to model

Revision ID: 7526ef5a88ed
Revises:
Create Date: 2026-06-15 09:27:13.581220

Adds a counter used by passive billing-failure downgrade: when a free-flagged
model fails repeatedly with 401/403 (auth/billing errors), this counter
increments; after BILLING_FAILURE_THRESHOLD (default 3) the model is
downgraded out of the free pool. A successful call resets it to 0.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7526ef5a88ed'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('model', schema=None) as batch_op:
        # server_default ensures existing rows get a value during ALTER on SQLite.
        batch_op.add_column(
            sa.Column(
                'consecutive_billing_failures',
                sa.Integer(),
                nullable=False,
                server_default='0',
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('model', schema=None) as batch_op:
        batch_op.drop_column('consecutive_billing_failures')
