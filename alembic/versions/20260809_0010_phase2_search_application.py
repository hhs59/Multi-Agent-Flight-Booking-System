"""phase 2 search application persistence

Revision ID: 20260809_0010
Revises: 20260809_0009
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260809_0010"
down_revision: str | Sequence[str] | None = "20260809_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "flight_offers", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )

    op.create_table(
        "flight_discoveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("resolved_request", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id"),
        sa.CheckConstraint(
            "status IN ('pending', 'results', 'no_results', 'provider_unavailable')",
            name="ck_flight_discoveries_status",
        ),
    )
    op.create_index(
        "ix_flight_discoveries_user_id", "flight_discoveries", ["user_id"], unique=False
    )
    op.create_index(
        "ix_flight_discoveries_user_created",
        "flight_discoveries",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "flight_search_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("discovery_id", sa.Uuid(), nullable=False),
        sa.Column("search_id", sa.Uuid(), nullable=True),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("safe_error_code", sa.String(length=80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["discovery_id", "user_id"],
            ["flight_discoveries.id", "flight_discoveries.user_id"],
            ondelete="CASCADE",
            name="fk_flight_search_attempts_discovery_owner",
        ),
        sa.ForeignKeyConstraint(
            ["search_id", "user_id"],
            ["flight_searches.id", "flight_searches.user_id"],
            ondelete="CASCADE",
            name="fk_flight_search_attempts_search_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id"),
        sa.CheckConstraint(
            "outcome IN ('results', 'no_results', 'provider_error')",
            name="ck_flight_search_attempts_outcome",
        ),
        sa.CheckConstraint(
            "result_count >= 0", name="ck_flight_search_attempts_nonnegative_result_count"
        ),
    )
    op.create_index(
        "ix_flight_search_attempts_user_id", "flight_search_attempts", ["user_id"], unique=False
    )
    op.create_index(
        "ix_flight_search_attempts_discovery_id",
        "flight_search_attempts",
        ["discovery_id"],
        unique=False,
    )
    op.create_index(
        "ix_flight_search_attempts_search_id", "flight_search_attempts", ["search_id"], unique=False
    )
    op.create_index(
        "ix_flight_search_attempts_user_discovery",
        "flight_search_attempts",
        ["user_id", "discovery_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_flight_search_attempts_user_discovery", table_name="flight_search_attempts")
    op.drop_index("ix_flight_search_attempts_search_id", table_name="flight_search_attempts")
    op.drop_index("ix_flight_search_attempts_discovery_id", table_name="flight_search_attempts")
    op.drop_index("ix_flight_search_attempts_user_id", table_name="flight_search_attempts")
    op.drop_table("flight_search_attempts")
    op.drop_index("ix_flight_discoveries_user_created", table_name="flight_discoveries")
    op.drop_index("ix_flight_discoveries_user_id", table_name="flight_discoveries")
    op.drop_table("flight_discoveries")
    op.drop_column("flight_offers", "version")
