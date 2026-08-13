"""retain the owned offer used by a watch match

Revision ID: 20260810_0014
Revises: 20260810_0013
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0014"
down_revision: str | Sequence[str] | None = "20260810_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_INDEX = "ix_watch_matches_source_offer"
_SOURCE_CONSTRAINT = "match_offer_owner"


def upgrade() -> None:
    op.add_column("watch_matches", sa.Column("source_offer_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        _SOURCE_CONSTRAINT,
        "watch_matches",
        "flight_offers",
        ["source_offer_id", "user_id"],
        ["id", "user_id"],
    )
    op.create_index(_SOURCE_INDEX, "watch_matches", ["source_offer_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    referenced = bind.execute(
        sa.text("SELECT id FROM watch_matches WHERE source_offer_id IS NOT NULL LIMIT 1")
    ).first()
    if referenced is not None:
        raise RuntimeError(
            "cannot downgrade 20260810_0014: watch matches contain owned offer provenance; "
            "dropping the column would make confirmation/auto-buy unable to identify the "
            "same search-scoped offer. Roll back compatible worker traffic first or use an "
            "approved forward migration."
        )
    op.drop_index(_SOURCE_INDEX, table_name="watch_matches")
    op.drop_constraint(_SOURCE_CONSTRAINT, "watch_matches", type_="foreignkey")
    op.drop_column("watch_matches", "source_offer_id")
