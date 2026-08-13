"""phase 4 durable conversations and profiles

Revision ID: 20260802_0002
Revises: 20260724_0001
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260802_0002"
down_revision: str | None = "20260724_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_threads", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "chat_threads",
        sa.Column("summary_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "chat_threads",
        sa.Column("summary_prompt_version", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "chat_threads",
        sa.Column(
            "summarized_through_sequence",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "nonnegative_summary_version",
        "chat_threads",
        "summary_version >= 0 AND summarized_through_sequence >= 0",
    )
    op.alter_column("chat_threads", "summary_version", server_default=None)
    op.alter_column("chat_threads", "summarized_through_sequence", server_default=None)

    op.add_column(
        "agent_checkpoints",
        sa.Column("last_message_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "last_message_owner",
        "agent_checkpoints",
        "chat_messages",
        ["last_message_id", "user_id"],
        ["id", "user_id"],
    )

    op.alter_column("traveler_profiles", "legal_name", nullable=True)
    op.alter_column("traveler_profiles", "email", nullable=True)
    op.alter_column("traveler_profiles", "birth_date_encrypted", nullable=True)
    op.add_column(
        "traveler_profiles",
        sa.Column(
            "completeness",
            sa.String(length=32),
            server_default="incomplete",
            nullable=False,
        ),
    )
    op.add_column(
        "traveler_profiles",
        sa.Column(
            "save_preference",
            sa.String(length=32),
            server_default="ask",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "completeness",
        "traveler_profiles",
        "completeness IN ('incomplete', 'ready_domestic', 'ready_international')",
    )
    op.create_check_constraint(
        "save_preference",
        "traveler_profiles",
        "save_preference IN ('ask', 'allow_chat_save')",
    )
    op.alter_column("traveler_profiles", "completeness", server_default=None)
    op.alter_column("traveler_profiles", "save_preference", server_default=None)

    op.add_column(
        "booking_intents",
        sa.Column("traveler_snapshots_encrypted", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "booking_intents",
        sa.Column("snapshot_encryption_key_version", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "snapshot_encryption_pair",
        "booking_intents",
        "(traveler_snapshots_encrypted IS NULL) = (snapshot_encryption_key_version IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "snapshot_encryption_pair",
        "booking_intents",
        type_="check",
    )
    op.drop_column("booking_intents", "snapshot_encryption_key_version")
    op.drop_column("booking_intents", "traveler_snapshots_encrypted")

    op.drop_constraint(
        "save_preference",
        "traveler_profiles",
        type_="check",
    )
    op.drop_constraint(
        "completeness",
        "traveler_profiles",
        type_="check",
    )
    op.drop_column("traveler_profiles", "save_preference")
    op.drop_column("traveler_profiles", "completeness")
    # Phase 4 permits incomplete profiles, so existing rows may legitimately contain
    # NULL values in these columns. A downgrade must not invent PII or delete profiles
    # merely to recreate the stricter pre-Phase-4 constraints; keep the columns nullable
    # and let the next upgrade restore the Phase 4 application contract.

    op.drop_constraint(
        "last_message_owner",
        "agent_checkpoints",
        type_="foreignkey",
    )
    op.drop_column("agent_checkpoints", "last_message_id")

    op.drop_constraint(
        "nonnegative_summary_version",
        "chat_threads",
        type_="check",
    )
    op.drop_column("chat_threads", "summarized_through_sequence")
    op.drop_column("chat_threads", "summary_prompt_version")
    op.drop_column("chat_threads", "summary_version")
    op.drop_column("chat_threads", "summary")
