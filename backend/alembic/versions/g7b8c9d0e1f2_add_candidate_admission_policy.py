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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        column["name"] for column in inspector.get_columns("candidateprovider")
    }
    columns = [
        sa.Column("access_type", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("requires_card", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "admission_status",
            sa.String(),
            nullable=False,
            server_default="review_required",
        ),
        sa.Column("exclusion_reason", sa.String(), nullable=True),
    ]
    missing_columns = [column for column in columns if column.name not in existing_columns]

    if missing_columns:
        with op.batch_alter_table("candidateprovider", schema=None) as batch_op:
            for column in missing_columns:
                batch_op.add_column(column)

    # create_all() in older AC startup code could pre-create this table from
    # current metadata before Alembic reached this revision. Create indexes
    # separately and only when absent so that state, and partially repaired
    # databases, can continue upgrading safely.
    inspector = sa.inspect(bind)
    existing_indexes = {
        index["name"] for index in inspector.get_indexes("candidateprovider")
    }
    for index_name, column_name in [
        ("ix_candidateprovider_access_type", "access_type"),
        ("ix_candidateprovider_admission_status", "admission_status"),
    ]:
        if index_name not in existing_indexes:
            op.create_index(index_name, "candidateprovider", [column_name], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("candidateprovider", schema=None) as batch_op:
        batch_op.drop_index("ix_candidateprovider_admission_status")
        batch_op.drop_index("ix_candidateprovider_access_type")
        batch_op.drop_column("exclusion_reason")
        batch_op.drop_column("admission_status")
        batch_op.drop_column("requires_card")
        batch_op.drop_column("access_type")
