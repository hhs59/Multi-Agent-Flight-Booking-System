"""user-owned travel preferences

Revision ID: 20260809_0011
Revises: 20260809_0010
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0011"
down_revision: str | Sequence[str] | None = "20260809_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_travel_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("default_origin_airport", sa.String(length=3), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("preferred_cabin", sa.String(length=32), nullable=True),
        sa.Column("max_stops", sa.SmallInteger(), nullable=True),
        sa.Column("baggage_required", sa.Boolean(), nullable=True),
        sa.Column("preferred_departure_start", sa.Time(), nullable=True),
        sa.Column("preferred_departure_end", sa.Time(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.CheckConstraint(
            "max_stops IS NULL OR max_stops BETWEEN 0 AND 4",
            name="ck_user_travel_preferences_max_stops_range",
        ),
        sa.CheckConstraint(
            "(preferred_departure_start IS NULL) = (preferred_departure_end IS NULL)",
            name="ck_user_travel_preferences_departure_window_pair",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_user_travel_preferences_positive_version",
        ),
        sa.CheckConstraint(
            "preferred_cabin IS NULL OR preferred_cabin IN "
            "('economy', 'premium_economy', 'business', 'first')",
            name="ck_user_travel_preferences_cabin",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_travel_preferences")
