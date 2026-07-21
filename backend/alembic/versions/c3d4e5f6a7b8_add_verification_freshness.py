"""add verification freshness and channel status

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-20 00:00:00.000000

Adds evidence-backed freshness fields. Existing rows are backfilled only from
successful historical calls; migration time is deliberately not treated as a
verification event.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_verified_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("verification_method", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("staleness_threshold_days", sa.Integer(), nullable=False, server_default="7")
        )
        batch_op.add_column(sa.Column("free_expires_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_model_last_verified_at", ["last_verified_at"], unique=False)

    with op.batch_alter_table("channel", schema=None) as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(), nullable=False, server_default="active"))
        batch_op.add_column(sa.Column("status_reason", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("status_changed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("key_expires_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_channel_status", ["status"], unique=False)

    with op.batch_alter_table("healthrecord", schema=None) as batch_op:
        batch_op.add_column(sa.Column("verification_method", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("http_status", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("check_run_id", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("failure_reason", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_snapshot", sa.String(), nullable=True))
        batch_op.create_index("ix_healthrecord_check_run_id", ["check_run_id"], unique=False)
        batch_op.create_index(
            "ix_healthrecord_model_checked_at", ["model_id", "checked_at"], unique=False
        )

    # Preserve what is knowable from legacy data without inventing freshness.
    op.execute(
        """
        UPDATE healthrecord
        SET verification_method = CASE
            WHEN is_passive = 1 THEN 'passive'
            ELSE 'active_legacy'
        END,
        failure_reason = error_code
        """
    )
    op.execute(
        """
        UPDATE model
        SET last_verified_at = COALESCE(
                last_success_at,
                (
                    SELECT MAX(hr.checked_at)
                    FROM healthrecord AS hr
                    WHERE hr.model_id = model.id
                      AND hr.status IN ('healthy', 'slow')
                      AND hr.error_code IS NULL
                )
            )
        """
    )
    op.execute(
        """
        UPDATE model
        SET verification_method = (
            SELECT CASE
                WHEN hr.is_passive = 1 THEN 'passive'
                ELSE 'active_legacy'
            END
            FROM healthrecord AS hr
            WHERE hr.model_id = model.id
              AND hr.status IN ('healthy', 'slow')
              AND hr.error_code IS NULL
            ORDER BY hr.checked_at DESC
            LIMIT 1
        )
        WHERE last_verified_at IS NOT NULL
        """
    )
    op.execute("UPDATE channel SET status_changed_at = created_at WHERE status_changed_at IS NULL")
    with op.batch_alter_table("channel", schema=None) as batch_op:
        batch_op.alter_column(
            "status_changed_at",
            existing_type=sa.DateTime(),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("healthrecord", schema=None) as batch_op:
        batch_op.drop_index("ix_healthrecord_model_checked_at")
        batch_op.drop_index("ix_healthrecord_check_run_id")
        batch_op.drop_column("rate_limit_snapshot")
        batch_op.drop_column("failure_reason")
        batch_op.drop_column("check_run_id")
        batch_op.drop_column("http_status")
        batch_op.drop_column("verification_method")

    with op.batch_alter_table("channel", schema=None) as batch_op:
        batch_op.drop_index("ix_channel_status")
        batch_op.drop_column("key_expires_at")
        batch_op.drop_column("status_changed_at")
        batch_op.drop_column("status_reason")
        batch_op.drop_column("status")

    with op.batch_alter_table("model", schema=None) as batch_op:
        batch_op.drop_index("ix_model_last_verified_at")
        batch_op.drop_column("free_expires_at")
        batch_op.drop_column("staleness_threshold_days")
        batch_op.drop_column("verification_method")
        batch_op.drop_column("last_verified_at")
