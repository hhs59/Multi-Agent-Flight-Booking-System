"""phase 5 user-scoped provider offer identity

Revision ID: 20260802_0003
Revises: 20260802_0002
Create Date: 2026-08-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0003"
down_revision: str | None = "20260802_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_flight_offers_provider",
        "flight_offers",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_flight_offers_user_id",
        "flight_offers",
        ["user_id", "provider", "environment", "provider_offer_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_flight_offers_user_id",
        "flight_offers",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_flight_offers_provider",
        "flight_offers",
        ["provider", "environment", "provider_offer_id"],
    )
