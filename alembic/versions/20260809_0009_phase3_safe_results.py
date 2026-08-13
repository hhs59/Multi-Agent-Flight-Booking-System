"""phase 3 durable structured assistant results

Revision ID: 20260809_0009
Revises: 20260802_0008
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0009"
down_revision: str | Sequence[str] | None = "20260802_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("safe_result", sa.JSON(), nullable=True))
    op.add_column(
        "chat_messages",
        sa.Column("safe_result_schema_version", sa.Integer(), nullable=True),
    )
    op.add_column("chat_messages", sa.Column("safe_errors", sa.JSON(), nullable=True))
    op.create_check_constraint(
        "safe_result_assistant_only",
        "chat_messages",
        "role = 'assistant' OR (safe_result IS NULL AND safe_result_schema_version IS NULL)",
    )
    op.create_check_constraint(
        "safe_result_schema_version",
        "chat_messages",
        "safe_result_schema_version IS NULL OR safe_result_schema_version = 1",
    )


def downgrade() -> None:
    op.drop_constraint("safe_result_schema_version", "chat_messages", type_="check")
    op.drop_constraint("safe_result_assistant_only", "chat_messages", type_="check")
    op.drop_column("chat_messages", "safe_errors")
    op.drop_column("chat_messages", "safe_result_schema_version")
    op.drop_column("chat_messages", "safe_result")
