"""add channel provenance and compliance snapshot

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-21 11:20:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_REVIEWS = {
    "groq": ("medium", "官方条款限制未经批准的出售、转售、再许可、转让或分发；仅限个人自用。", "https://console.groq.com/docs/legal/services-agreement"),
    "siliconflow": ("unknown", "公开文档中未查到个人自用 API 聚合转发的明确授权或禁止条款；仅限个人自用并待复核。", "https://docs.siliconflow.cn/"),
    "openrouter": ("high", "当前条款明确禁止为转售模型 API 访问或开发竞争服务而使用；仅限个人自用。", "https://openrouter.ai/terms"),
    "zhipu": ("medium", "用户协议限制未经明示同意的转让、出售或提供他人使用；仅限个人本机调用。", "https://docs.bigmodel.cn/cn/terms/user-agreement"),
    "agnes": ("unknown", "未找到可公开访问且明确覆盖 API 聚合转发的官方条款；仅限个人自用并待复核。", "https://agnes-ai.com/"),
    "mistral": ("medium", "条款允许向 End Users 提供集成能力，但禁止买卖或转让 API Key；仅限个人自用。", "https://legal.mistral.ai/terms/commercial-terms-of-service"),
}


def _snapshot(risk: str, note: str, source: str = "") -> str:
    return json.dumps(
        {
            "risk": risk,
            "note": note,
            "reviewed_at": "2026-07-21",
            "sources": [source] if source else [],
        },
        ensure_ascii=False,
    )


def upgrade() -> None:
    with op.batch_alter_table("channel", schema=None) as batch_op:
        batch_op.add_column(sa.Column("config_type", sa.String(), nullable=False, server_default="custom_adapter"))
        batch_op.add_column(sa.Column("discovery_source", sa.String(), nullable=False, server_default="manual"))
        batch_op.add_column(sa.Column("compliance_note", sa.String(), nullable=False, server_default="未完成合规审核"))
        batch_op.create_index("ix_channel_config_type", ["config_type"], unique=False)
        batch_op.create_index("ix_channel_discovery_source", ["discovery_source"], unique=False)

    connection = op.get_bind()
    connection.execute(
        sa.text("UPDATE channel SET config_type = 'declarative' WHERE provider_type = 'mistral'")
    )
    for provider_id, review in _REVIEWS.items():
        connection.execute(
            sa.text(
                "UPDATE channel SET compliance_note = :note WHERE provider_type = :provider_id"
            ),
            {"note": _snapshot(*review), "provider_id": provider_id},
        )
    connection.execute(
        sa.text(
            "UPDATE channel SET compliance_note = :note WHERE compliance_note = '未完成合规审核'"
        ),
        {
            "note": _snapshot(
                "unknown",
                "尚未找到该厂商的明确代理/聚合条款，必须在继续使用前人工复核。",
                "",
            )
        },
    )


def downgrade() -> None:
    with op.batch_alter_table("channel", schema=None) as batch_op:
        batch_op.drop_index("ix_channel_discovery_source")
        batch_op.drop_index("ix_channel_config_type")
        batch_op.drop_column("compliance_note")
        batch_op.drop_column("discovery_source")
        batch_op.drop_column("config_type")
