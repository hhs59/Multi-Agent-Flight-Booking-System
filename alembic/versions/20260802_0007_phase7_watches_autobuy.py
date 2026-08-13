"""phase 7 durable watches, mandates, holds, and notifications

Revision ID: 20260802_0007
Revises: 20260802_0006
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260802_0007"
down_revision: str | None = "20260802_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        sa.Column("lease_owner", sa.String(120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("run_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_code", sa.String(120), nullable=True),
    ):
        op.add_column("flight_watches", column)
    op.alter_column("flight_watches", "consecutive_failures", server_default=None)
    op.alter_column("flight_watches", "run_count", server_default=None)
    op.create_index("ix_flight_watches_lease", "flight_watches", ["lease_expires_at", "status"])

    for column in (
        sa.Column("provider", sa.String(80), nullable=True),
        sa.Column("outcome", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("backoff_seconds", sa.Integer(), server_default="0", nullable=False),
    ):
        op.add_column("watch_runs", column)
    op.alter_column("watch_runs", "outcome", server_default=None)
    op.alter_column("watch_runs", "backoff_seconds", server_default=None)

    for column in (
        sa.Column("provider", sa.String(80), nullable=True),
        sa.Column("environment", sa.String(16), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), server_default="matched", nullable=False),
        sa.Column("match_reasons", sa.JSON(), server_default="[]", nullable=False),
        sa.Column("rejection_reason", sa.String(240), nullable=True),
    ):
        op.add_column("watch_matches", column)
    op.alter_column("watch_matches", "status", server_default=None)
    op.alter_column("watch_matches", "match_reasons", server_default=None)

    op.create_table(
        "purchase_mandates",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("watch_id", sa.Uuid(), nullable=False),
        sa.Column("traveler_profile_ids", sa.JSON(), nullable=False),
        sa.Column("criteria_snapshot", sa.JSON(), nullable=False),
        sa.Column("maximum_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("purchase_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payment_method_reference_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("payment_reference_key_version", sa.Integer(), nullable=False),
        sa.Column(
            "off_session_permission", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("terms_version", sa.String(80), nullable=False),
        sa.Column("consent_version", sa.String(40), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
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
            ["watch_id", "user_id"],
            ["flight_watches.id", "flight_watches.user_id"],
            name="mandate_watch_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_mandates")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_purchase_mandates_id")),
        sa.UniqueConstraint("watch_id", "version", name=op.f("uq_purchase_mandates_watch_id")),
    )
    op.create_index("ix_purchase_mandates_user_status", "purchase_mandates", ["user_id", "status"])

    op.create_table(
        "watch_holds",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("watch_id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("provider_hold_id_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("provider_reference_key_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
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
            ["watch_id", "user_id"],
            ["flight_watches.id", "flight_watches.user_id"],
            name="hold_watch_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["match_id", "user_id"],
            ["watch_matches.id", "watch_matches.user_id"],
            name="hold_match_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watch_holds")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_watch_holds_id")),
        sa.UniqueConstraint("watch_id", "match_id", name=op.f("uq_watch_holds_watch_id")),
    )
    op.create_index("ix_watch_holds_expiry", "watch_holds", ["status", "expires_at"])

    op.create_table(
        "watch_notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("watch_id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("destination_hash", sa.String(64), nullable=False),
        sa.Column("provider_message_reference", sa.String(512), nullable=True),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["watch_id", "user_id"],
            ["flight_watches.id", "flight_watches.user_id"],
            name="notification_watch_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["match_id", "user_id"],
            ["watch_matches.id", "watch_matches.user_id"],
            name="notification_match_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watch_notifications")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_watch_notifications_id")),
        sa.UniqueConstraint("match_id", "channel", name=op.f("uq_watch_notifications_match_id")),
    )
    op.create_index("ix_watch_notifications_status", "watch_notifications", ["status", "sent_at"])


def downgrade() -> None:
    op.drop_index("ix_watch_notifications_status", table_name="watch_notifications")
    op.drop_table("watch_notifications")
    op.drop_index("ix_watch_holds_expiry", table_name="watch_holds")
    op.drop_table("watch_holds")
    op.drop_index("ix_purchase_mandates_user_status", table_name="purchase_mandates")
    op.drop_table("purchase_mandates")
    for column in (
        "rejection_reason",
        "match_reasons",
        "status",
        "expires_at",
        "environment",
        "provider",
    ):
        op.drop_column("watch_matches", column)
    for column in ("backoff_seconds", "outcome", "provider"):
        op.drop_column("watch_runs", column)
    op.drop_index("ix_flight_watches_lease", table_name="flight_watches")
    for column in (
        "last_error_code",
        "run_count",
        "consecutive_failures",
        "lease_expires_at",
        "lease_owner",
    ):
        op.drop_column("flight_watches", column)
