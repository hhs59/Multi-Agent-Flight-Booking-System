"""phase 6 durable booking workflow

Revision ID: 20260802_0004
Revises: 20260802_0003
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260802_0004"
down_revision: str | None = "20260802_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "booking_quotes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("booking_intent_id", sa.Uuid(), nullable=False),
        sa.Column("source_offer_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("quote_snapshot", sa.JSON(), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("version > 0", name=op.f("ck_booking_quotes_positive_version")),
        sa.ForeignKeyConstraint(
            ["booking_intent_id", "user_id"],
            ["booking_intents.id", "booking_intents.user_id"],
            name="quote_intent_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_offer_id", "user_id"],
            ["flight_offers.id", "flight_offers.user_id"],
            name="quote_offer_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking_quotes")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_booking_quotes_id")),
        sa.UniqueConstraint(
            "booking_intent_id", "version", name=op.f("uq_booking_quotes_booking_intent_id")
        ),
    )
    op.create_index(
        "ix_booking_quotes_user_expiry",
        "booking_quotes",
        ["user_id", "expires_at"],
        unique=False,
    )
    op.create_index(op.f("ix_booking_quotes_user_id"), "booking_quotes", ["user_id"], unique=False)

    op.add_column("booking_intents", sa.Column("current_quote_id", sa.Uuid(), nullable=True))
    op.add_column(
        "booking_intents",
        sa.Column("quote_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.alter_column("booking_intents", "quote_version", server_default=None)

    op.add_column("bookings", sa.Column("quote_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "booking_quote_owner",
        "bookings",
        "booking_quotes",
        ["quote_id", "user_id"],
        ["id", "user_id"],
    )
    op.add_column(
        "bookings",
        sa.Column("payment_method_reference_encrypted", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column("payment_reference_key_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column(
            "payment_authorization_reference_encrypted",
            sa.LargeBinary(),
            nullable=True,
        ),
    )
    op.add_column(
        "bookings",
        sa.Column("captured_payment_reference_encrypted", sa.LargeBinary(), nullable=True),
    )
    op.add_column("bookings", sa.Column("confirmation_code", sa.String(length=40), nullable=True))
    op.add_column(
        "bookings",
        sa.Column("confirmed_by_user_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("bookings", sa.Column("consent_snapshot", sa.JSON(), nullable=True))

    op.add_column(
        "booking_events",
        sa.Column("actor_type", sa.String(length=40), server_default="system", nullable=False),
    )
    op.add_column("booking_events", sa.Column("actor_id", sa.Uuid(), nullable=True))
    op.add_column("booking_events", sa.Column("from_status", sa.String(length=40), nullable=True))
    op.add_column("booking_events", sa.Column("to_status", sa.String(length=40), nullable=True))
    op.add_column(
        "booking_events", sa.Column("idempotency_key", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "booking_events",
        sa.Column("resulting_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.execute(
        sa.text(
            "UPDATE booking_events SET idempotency_key = id::text WHERE idempotency_key IS NULL"
        )
    )
    op.alter_column("booking_events", "actor_type", server_default=None)
    op.alter_column("booking_events", "resulting_version", server_default=None)
    op.alter_column("booking_events", "idempotency_key", nullable=False)
    op.create_unique_constraint(
        "uq_booking_events_booking_id",
        "booking_events",
        ["booking_id", "idempotency_key"],
    )

    op.create_table(
        "booking_operations",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("booking_intent_id", sa.Uuid(), nullable=True),
        sa.Column("booking_id", sa.Uuid(), nullable=True),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="processing", nullable=False),
        sa.Column("result", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["booking_intent_id", "user_id"],
            ["booking_intents.id", "booking_intents.user_id"],
            name="operation_intent_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id", "user_id"],
            ["bookings.id", "bookings.user_id"],
            name="operation_booking_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking_operations")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_booking_operations_id")),
        sa.UniqueConstraint(
            "idempotency_key",
            name=op.f("uq_booking_operations_idempotency_key"),
        ),
    )
    op.create_index(
        "ix_booking_operations_user_aggregate",
        "booking_operations",
        ["user_id", "booking_intent_id", "operation"],
        unique=False,
    )
    op.create_index(
        op.f("ix_booking_operations_user_id"),
        "booking_operations",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_booking_operations_user_id"), table_name="booking_operations")
    op.drop_index("ix_booking_operations_user_aggregate", table_name="booking_operations")
    op.drop_table("booking_operations")

    op.drop_constraint("uq_booking_events_booking_id", "booking_events", type_="unique")
    for column in (
        "resulting_version",
        "idempotency_key",
        "to_status",
        "from_status",
        "actor_id",
        "actor_type",
    ):
        op.drop_column("booking_events", column)

    for column in (
        "consent_snapshot",
        "confirmed_by_user_at",
        "confirmation_code",
        "captured_payment_reference_encrypted",
        "payment_authorization_reference_encrypted",
        "payment_reference_key_version",
        "payment_method_reference_encrypted",
    ):
        op.drop_column("bookings", column)
    op.drop_constraint("booking_quote_owner", "bookings", type_="foreignkey")
    op.drop_column("bookings", "quote_id")

    op.drop_column("booking_intents", "quote_version")
    op.drop_column("booking_intents", "current_quote_id")

    op.drop_index(op.f("ix_booking_quotes_user_id"), table_name="booking_quotes")
    op.drop_index("ix_booking_quotes_user_expiry", table_name="booking_quotes")
    op.drop_table("booking_quotes")
