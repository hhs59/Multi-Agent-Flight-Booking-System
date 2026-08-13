"""scope provider offer identity to an owning search

Revision ID: 20260810_0013
Revises: 20260810_0012
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0013"
down_revision: str | Sequence[str] | None = "20260810_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GLOBAL_CONSTRAINT = "uq_flight_offers_user_id"
_SCOPED_CONSTRAINT = "uq_flight_offers_search_provider_identity"
_SEARCH_INDEX = "ix_flight_offers_user_search"


def upgrade() -> None:
    op.drop_constraint(_GLOBAL_CONSTRAINT, "flight_offers", type_="unique")
    op.create_unique_constraint(
        _SCOPED_CONSTRAINT,
        "flight_offers",
        ["user_id", "search_id", "provider", "environment", "provider_offer_id"],
    )
    op.create_index(
        _SEARCH_INDEX,
        "flight_offers",
        ["user_id", "search_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            """
            SELECT user_id, provider, environment, provider_offer_id
            FROM flight_offers
            GROUP BY user_id, provider, environment, provider_offer_id
            HAVING COUNT(DISTINCT search_id) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "cannot downgrade 20260810_0013: valid search-scoped offer identities occur in "
            "multiple searches; restoring user/provider uniqueness would collapse independent "
            "offer memberships. Preserve the rows and roll back application traffic with "
            "compatible code or an approved forward migration."
        )
    op.drop_constraint(_SCOPED_CONSTRAINT, "flight_offers", type_="unique")
    op.drop_index(_SEARCH_INDEX, table_name="flight_offers")
    op.create_unique_constraint(
        _GLOBAL_CONSTRAINT,
        "flight_offers",
        ["user_id", "provider", "environment", "provider_offer_id"],
    )
