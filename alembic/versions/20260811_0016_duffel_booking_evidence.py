"""add structured traveler and provider booking evidence fields

Revision ID: 20260811_0016
Revises: 20260811_0015
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0016"
down_revision: str | Sequence[str] | None = "20260811_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TRAVELER_COLUMNS = ("title", "given_name", "family_name")
_BOOKING_COLUMNS = (
    "provider_environment",
    "provider_live_mode",
    "provider_status",
    "last_reconciled_at",
)


def upgrade() -> None:
    op.add_column("traveler_profiles", sa.Column("title", sa.String(length=20), nullable=True))
    op.add_column(
        "traveler_profiles", sa.Column("given_name", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "traveler_profiles", sa.Column("family_name", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "bookings", sa.Column("provider_environment", sa.String(length=16), nullable=True)
    )
    op.add_column("bookings", sa.Column("provider_live_mode", sa.Boolean(), nullable=True))
    op.add_column("bookings", sa.Column("provider_status", sa.String(length=80), nullable=True))
    op.add_column(
        "bookings", sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    bind = op.get_bind()
    structured = bind.execute(
        sa.text(
            "SELECT id FROM traveler_profiles WHERE title IS NOT NULL OR given_name IS NOT NULL "
            "OR family_name IS NOT NULL LIMIT 1"
        )
    ).first()
    evidence = bind.execute(
        sa.text(
            "SELECT id FROM bookings WHERE provider_environment IS NOT NULL "
            "OR provider_live_mode IS NOT NULL OR provider_status IS NOT NULL "
            "OR last_reconciled_at IS NOT NULL LIMIT 1"
        )
    ).first()
    if structured is not None or evidence is not None:
        raise RuntimeError(
            "cannot downgrade 20260811_0016: structured traveler names or provider booking "
            "evidence would be lost"
        )
    for column in reversed(_BOOKING_COLUMNS):
        op.drop_column("bookings", column)
    for column in reversed(_TRAVELER_COLUMNS):
        op.drop_column("traveler_profiles", column)
