"""bind current quote to the owning intent

Revision ID: 20260802_0006
Revises: 20260802_0005
Create Date: 2026-08-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0006"
down_revision: str | None = "20260802_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_foreign_key(
        "current_quote_owner",
        "booking_intents",
        "booking_quotes",
        ["current_quote_id", "user_id"],
        ["id", "user_id"],
    )


def downgrade() -> None:
    op.drop_constraint("current_quote_owner", "booking_intents", type_="foreignkey")
