"""scope booking-intent idempotency by authenticated user

Revision ID: 20260810_0012
Revises: 20260809_0011
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0012"
down_revision: str | Sequence[str] | None = "20260809_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_booking_intents_idempotency_key",
        "booking_intents",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_booking_intents_user_idempotency_key",
        "booking_intents",
        ["user_id", "idempotency_key"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            """
            SELECT idempotency_key
            FROM booking_intents
            GROUP BY idempotency_key
            HAVING COUNT(DISTINCT user_id) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "cannot downgrade 20260810_0012: valid user-scoped idempotency keys are shared by "
            "multiple users; automatic downgrade would lose retry semantics. Roll back application "
            "traffic with feature flags/compatible code or use an approved forward migration."
        )
    op.drop_constraint(
        "uq_booking_intents_user_idempotency_key",
        "booking_intents",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_booking_intents_idempotency_key",
        "booking_intents",
        ["idempotency_key"],
    )
