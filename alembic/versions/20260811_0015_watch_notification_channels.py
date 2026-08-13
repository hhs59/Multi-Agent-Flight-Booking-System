"""add durable in-app watch notification delivery fields

Revision ID: 20260811_0015
Revises: 20260810_0014
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0015"
down_revision: str | Sequence[str] | None = "20260810_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DELIVERY_CONSTRAINT = "watch_notification_delivery_fields"


def upgrade() -> None:
    op.alter_column(
        "watch_notifications",
        "destination_hash",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.create_check_constraint(
        _DELIVERY_CONSTRAINT,
        "watch_notifications",
        "(channel = 'in_app' AND destination_hash IS NULL AND provider_message_reference IS NULL) "
        "OR (channel IN ('email', 'sms') AND destination_hash IS NOT NULL)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    in_app = bind.execute(
        sa.text("SELECT id FROM watch_notifications WHERE channel = 'in_app' LIMIT 1")
    ).first()
    if in_app is not None:
        raise RuntimeError(
            "cannot downgrade 20260811_0015: in-app watch notifications exist and cannot be "
            "represented by the previous external-delivery schema"
        )
    op.drop_constraint(_DELIVERY_CONSTRAINT, "watch_notifications", type_="check")
    op.alter_column(
        "watch_notifications",
        "destination_hash",
        existing_type=sa.String(length=64),
        nullable=False,
    )
