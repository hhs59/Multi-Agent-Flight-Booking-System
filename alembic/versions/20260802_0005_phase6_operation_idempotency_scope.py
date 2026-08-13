"""scope booking operation idempotency to each user

Revision ID: 20260802_0005
Revises: 20260802_0004
Create Date: 2026-08-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0005"
down_revision: str | None = "20260802_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_booking_operations_idempotency_key", "booking_operations", type_="unique"
    )
    op.create_unique_constraint(
        "uq_booking_operations_user_idempotency_key",
        "booking_operations",
        ["user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_booking_operations_user_idempotency_key", "booking_operations", type_="unique"
    )
    op.create_unique_constraint(
        "uq_booking_operations_idempotency_key",
        "booking_operations",
        ["idempotency_key"],
    )
