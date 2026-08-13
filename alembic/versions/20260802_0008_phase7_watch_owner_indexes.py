"""add owner indexes for phase 7 records

Revision ID: 20260802_0008
Revises: 20260802_0007
Create Date: 2026-08-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0008"
down_revision: str | None = "20260802_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_purchase_mandates_user_id", "purchase_mandates", ["user_id"])
    op.create_index("ix_watch_holds_user_id", "watch_holds", ["user_id"])
    op.create_index("ix_watch_notifications_user_id", "watch_notifications", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_watch_notifications_user_id", table_name="watch_notifications")
    op.drop_index("ix_watch_holds_user_id", table_name="watch_holds")
    op.drop_index("ix_purchase_mandates_user_id", table_name="purchase_mandates")
