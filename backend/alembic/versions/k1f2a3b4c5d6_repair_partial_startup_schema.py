"""repair schemas left partial by create_all-before-migrate startup

Revision ID: k1f2a3b4c5d6
Revises: j0e1f2a3b4c5
Create Date: 2026-07-28 12:00:00.000000

Older AC startup code called SQLModel.create_all() before Alembic. On an
upgrade, that could create newly introduced tables at the latest schema while
leaving new columns absent from existing tables. The g7 migration then failed
while trying to add columns that create_all had already placed on
candidateprovider, preventing the i9 and j0 migrations from running.

This revision is intentionally idempotent. It repairs databases that were
stamped at head after a manual workaround as well as databases that contain
only part of the expected schema.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "k1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "j0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(inspector, table_name: str) -> set[str]:
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("apikey") and "allowed_profiles" not in _column_names(
        inspector, "apikey"
    ):
        with op.batch_alter_table("apikey", schema=None) as batch_op:
            batch_op.add_column(sa.Column("allowed_profiles", sa.String(), nullable=True))

    inspector = sa.inspect(bind)
    if inspector.has_table("model") and "consecutive_errors" not in _column_names(
        inspector, "model"
    ):
        with op.batch_alter_table("model", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "consecutive_errors",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )

    inspector = sa.inspect(bind)
    if not inspector.has_table("candidateprovider"):
        return

    candidate_columns = [
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
    existing_columns = _column_names(inspector, "candidateprovider")
    missing_columns = [
        column for column in candidate_columns if column.name not in existing_columns
    ]
    if missing_columns:
        with op.batch_alter_table("candidateprovider", schema=None) as batch_op:
            for column in missing_columns:
                batch_op.add_column(column)

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
    # This revision repairs objects owned by earlier migrations. Downgrading to
    # j0 must preserve them because j0's schema already expects them.
    pass
