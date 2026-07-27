"""add model.consecutive_errors for transient-error tolerance

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-07-27 00:00:01.000000

Adds ``consecutive_errors`` to the ``model`` table. A single transient 5xx /
network error no longer marks a model ``down`` (which evicts it from the
candidate pool and relies on a slow probe to restore). Instead the count is
incremented; only a threshold in a row demotes the model to ``down``. Any
success resets it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "j0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "i9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("consecutive_errors", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("model", schema=None) as batch_op:
        batch_op.drop_column("consecutive_errors")
