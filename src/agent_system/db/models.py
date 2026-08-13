from __future__ import annotations

from datetime import datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agent_system.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin, utc_now


class UserRecord(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject"),
        CheckConstraint(
            "status IN ('active', 'suspended', 'pending_deletion')",
            name="status",
        ),
    )

    oidc_issuer: Mapped[str] = mapped_column(String(2048), nullable=False)
    oidc_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="vi")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Ho_Chi_Minh")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class UserSessionRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("session_token_hash"),
        UniqueConstraint("id", "user_id"),
        Index("ix_user_sessions_user_expiry", "user_id", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device_label: Mapped[str | None] = mapped_column(String(120))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))


class TravelerProfileRecord(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "traveler_profiles"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("user_id", "label"),
        Index(
            "uq_traveler_profiles_one_default_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default = 1"),
        ),
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint(
            "completeness IN ('incomplete', 'ready_domestic', 'ready_international')",
            name="completeness",
        ),
        CheckConstraint(
            "save_preference IN ('ask', 'allow_chat_save')",
            name="save_preference",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(20))
    given_name: Mapped[str | None] = mapped_column(String(120))
    family_name: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(40))
    gender_marker: Mapped[str | None] = mapped_column(String(30))
    birth_date_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    nationality_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    passport_number_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    passport_issuing_country_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    passport_expiry_date_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    encryption_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    consent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completeness: Mapped[str] = mapped_column(String(32), nullable=False, default="incomplete")
    save_preference: Mapped[str] = mapped_column(String(32), nullable=False, default="ask")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class UserTravelPreferenceRecord(TimestampMixin, Base):
    __tablename__ = "user_travel_preferences"
    __table_args__ = (
        CheckConstraint(
            "max_stops IS NULL OR max_stops BETWEEN 0 AND 4",
            name="max_stops_range",
        ),
        CheckConstraint(
            "(preferred_departure_start IS NULL) = (preferred_departure_end IS NULL)",
            name="departure_window_pair",
        ),
        CheckConstraint(
            "version >= 1",
            name="positive_version",
        ),
        CheckConstraint(
            "preferred_cabin IS NULL OR preferred_cabin IN "
            "('economy', 'premium_economy', 'business', 'first')",
            name="cabin",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    default_origin_airport: Mapped[str | None] = mapped_column(String(3))
    timezone: Mapped[str | None] = mapped_column(String(64))
    preferred_cabin: Mapped[str | None] = mapped_column(String(32))
    max_stops: Mapped[int | None] = mapped_column(SmallInteger)
    baggage_required: Mapped[bool | None] = mapped_column(Boolean)
    preferred_departure_start: Mapped[time | None] = mapped_column(Time)
    preferred_departure_end: Mapped[time | None] = mapped_column(Time)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class ChatThreadRecord(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chat_threads"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint(
            "summary_version >= 0 AND summarized_through_sequence >= 0",
            name="nonnegative_summary_version",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(200))
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="vi")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary: Mapped[str | None] = mapped_column(Text)
    summary_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_prompt_version: Mapped[str | None] = mapped_column(String(80))
    summarized_through_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ChatMessageRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["chat_threads.id", "chat_threads.user_id"],
            ondelete="CASCADE",
            name="thread_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("thread_id", "sequence", name="uq_chat_messages_thread_sequence"),
        UniqueConstraint(
            "thread_id",
            "client_message_id",
            name="uq_chat_messages_thread_client_message",
        ),
        CheckConstraint("role IN ('user', 'assistant', 'system', 'tool')", name="role"),
        CheckConstraint(
            "role = 'assistant' OR (safe_result IS NULL AND safe_result_schema_version IS NULL)",
            name="safe_result_assistant_only",
        ),
        CheckConstraint(
            "safe_result_schema_version IS NULL OR safe_result_schema_version = 1",
            name="safe_result_schema_version",
        ),
        Index("ix_chat_messages_thread_chronology", "thread_id", "sequence", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    thread_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    client_message_id: Mapped[str | None] = mapped_column(String(120))
    safe_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    safe_result_schema_version: Mapped[int | None] = mapped_column(Integer)
    safe_errors: Mapped[list[str] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )


class AgentCheckpointRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["chat_threads.id", "chat_threads.user_id"],
            ondelete="CASCADE",
            name="thread_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("thread_id", "version"),
        ForeignKeyConstraint(
            ["last_message_id", "user_id"],
            ["chat_messages.id", "chat_messages.user_id"],
            name="last_message_owner",
        ),
        CheckConstraint("version > 0", name="positive_version"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    thread_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_message_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )


class FlightSearchRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "flight_searches"
    __table_args__ = (UniqueConstraint("id", "user_id"),)

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    criteria: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class FlightOfferRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "flight_offers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["search_id", "user_id"],
            ["flight_searches.id", "flight_searches.user_id"],
            ondelete="CASCADE",
            name="search_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint(
            "user_id",
            "search_id",
            "provider",
            "environment",
            "provider_offer_id",
            name="uq_flight_offers_search_provider_identity",
        ),
        Index("ix_flight_offers_user_search", "user_id", "search_id"),
        Index("ix_flight_offers_user_expiry", "user_id", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    search_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_offer_id: Mapped[str] = mapped_column(String(512), nullable=False)
    offer_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class FlightDiscoveryRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "flight_discoveries"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint(
            "status IN ('pending', 'results', 'no_results', 'provider_unavailable')",
            name="status",
        ),
        Index("ix_flight_discoveries_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resolved_request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FlightSearchAttemptRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "flight_search_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["discovery_id", "user_id"],
            ["flight_discoveries.id", "flight_discoveries.user_id"],
            ondelete="CASCADE",
            name="discovery_owner",
        ),
        ForeignKeyConstraint(
            ["search_id", "user_id"],
            ["flight_searches.id", "flight_searches.user_id"],
            ondelete="CASCADE",
            name="search_owner",
        ),
        UniqueConstraint("id", "user_id"),
        CheckConstraint(
            "outcome IN ('results', 'no_results', 'provider_error')",
            name="outcome",
        ),
        CheckConstraint("result_count >= 0", name="nonnegative_result_count"),
        Index("ix_flight_search_attempts_user_discovery", "user_id", "discovery_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    discovery_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    search_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safe_error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BookingQuoteRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "booking_quotes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["booking_intent_id", "user_id"],
            ["booking_intents.id", "booking_intents.user_id"],
            ondelete="CASCADE",
            name="quote_intent_owner",
        ),
        ForeignKeyConstraint(
            ["source_offer_id", "user_id"],
            ["flight_offers.id", "flight_offers.user_id"],
            ondelete="RESTRICT",
            name="quote_offer_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("booking_intent_id", "version"),
        CheckConstraint("version > 0", name="positive_version"),
        Index("ix_booking_quotes_user_expiry", "user_id", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    booking_intent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_offer_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    quote_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    total_amount: Mapped[Any] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )


class BookingIntentRecord(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "booking_intents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["current_quote_id", "user_id"],
            ["booking_quotes.id", "booking_quotes.user_id"],
            name="current_quote_owner",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["source_offer_id", "user_id"],
            ["flight_offers.id", "flight_offers.user_id"],
            ondelete="CASCADE",
            name="offer_owner",
        ),
        ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["chat_threads.id", "chat_threads.user_id"],
            name="thread_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_booking_intents_user_idempotency_key",
        ),
        CheckConstraint("version > 0", name="positive_version"),
        Index("ix_booking_intents_user_status", "user_id", "status"),
        CheckConstraint(
            "(traveler_snapshots_encrypted IS NULL) = (snapshot_encryption_key_version IS NULL)",
            name="snapshot_encryption_pair",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_offer_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    thread_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    traveler_profile_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    traveler_snapshots_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    snapshot_encryption_key_version: Mapped[int | None] = mapped_column(Integer)
    current_quote_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    quote_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class BookingRecord(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["booking_intent_id", "user_id"],
            ["booking_intents.id", "booking_intents.user_id"],
            ondelete="CASCADE",
            name="intent_owner",
        ),
        ForeignKeyConstraint(
            ["quote_id", "user_id"],
            ["booking_quotes.id", "booking_quotes.user_id"],
            name="booking_quote_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("provider", "provider_order_id"),
        UniqueConstraint("idempotency_key"),
        CheckConstraint("version > 0", name="positive_version"),
        Index("ix_bookings_user_status", "user_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    booking_intent_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    quote_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    provider: Mapped[str | None] = mapped_column(String(80))
    provider_order_id: Mapped[str | None] = mapped_column(String(512))
    provider_environment: Mapped[str | None] = mapped_column(String(16))
    provider_live_mode: Mapped[bool | None] = mapped_column(Boolean)
    provider_status: Mapped[str | None] = mapped_column(String(80))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_customer_reference: Mapped[str | None] = mapped_column(String(512))
    payment_method_reference: Mapped[str | None] = mapped_column(String(512))
    payment_method_reference_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    payment_reference_key_version: Mapped[int | None] = mapped_column(Integer)
    payment_authorization_reference_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    captured_payment_reference_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    confirmation_code: Mapped[str | None] = mapped_column(String(40))
    confirmed_by_user_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class BookingEventRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "booking_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["booking_id", "user_id"],
            ["bookings.id", "bookings.user_id"],
            ondelete="CASCADE",
            name="booking_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("booking_id", "idempotency_key"),
        Index("ix_booking_events_booking_time", "booking_id", "occurred_at"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    booking_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False, default="system")
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str | None] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, default="event")
    resulting_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )


class BookingOperationRecord(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "booking_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["booking_intent_id", "user_id"],
            ["booking_intents.id", "booking_intents.user_id"],
            ondelete="CASCADE",
            name="operation_intent_owner",
        ),
        ForeignKeyConstraint(
            ["booking_id", "user_id"],
            ["bookings.id", "bookings.user_id"],
            ondelete="CASCADE",
            name="operation_booking_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_booking_operations_user_idempotency_key"
        ),
        Index("ix_booking_operations_user_aggregate", "user_id", "booking_intent_id", "operation"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    booking_intent_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    booking_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="processing")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class PurchaseMandateRecord(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "purchase_mandates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["watch_id", "user_id"],
            ["flight_watches.id", "flight_watches.user_id"],
            ondelete="CASCADE",
            name="mandate_watch_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("watch_id", "version"),
        Index("ix_purchase_mandates_user_status", "user_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    watch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    traveler_profile_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    criteria_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    maximum_amount: Mapped[Any] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    purchase_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payment_method_reference_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payment_reference_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    off_session_permission: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    terms_version: Mapped[str] = mapped_column(String(80), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WatchHoldRecord(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "watch_holds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["watch_id", "user_id"],
            ["flight_watches.id", "flight_watches.user_id"],
            ondelete="CASCADE",
            name="hold_watch_owner",
        ),
        ForeignKeyConstraint(
            ["match_id", "user_id"],
            ["watch_matches.id", "watch_matches.user_id"],
            ondelete="CASCADE",
            name="hold_match_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("watch_id", "match_id"),
        Index("ix_watch_holds_expiry", "status", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    watch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    match_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_hold_id_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    provider_reference_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)


class WatchNotificationRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "watch_notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["watch_id", "user_id"],
            ["flight_watches.id", "flight_watches.user_id"],
            ondelete="CASCADE",
            name="notification_watch_owner",
        ),
        ForeignKeyConstraint(
            ["match_id", "user_id"],
            ["watch_matches.id", "watch_matches.user_id"],
            ondelete="CASCADE",
            name="notification_match_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("match_id", "channel"),
        CheckConstraint(
            "(channel = 'in_app' AND destination_hash IS NULL AND provider_message_reference IS NULL) "
            "OR (channel IN ('email', 'sms') AND destination_hash IS NOT NULL)",
            name="notification_destination_by_channel",
        ),
        Index("ix_watch_notifications_status", "status", "sent_at"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    watch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    match_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_hash: Mapped[str | None] = mapped_column(String(64))
    provider_message_reference: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(120))


class FlightWatchRecord(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "flight_watches"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint("version > 0", name="positive_version"),
        Index("ix_flight_watches_due", "status", "next_run_at"),
        Index("ix_flight_watches_lease", "lease_expires_at", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    criteria: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="active")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(120))


class WatchRunRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "watch_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["watch_id", "user_id"],
            ["flight_watches.id", "flight_watches.user_id"],
            ondelete="CASCADE",
            name="watch_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("idempotency_key"),
        Index("ix_watch_runs_due_lease", "scheduled_for", "lease_expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    watch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    backoff_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WatchMatchRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "watch_matches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["watch_id", "user_id"],
            ["flight_watches.id", "flight_watches.user_id"],
            ondelete="CASCADE",
            name="watch_owner",
        ),
        ForeignKeyConstraint(
            ["source_offer_id", "user_id"],
            ["flight_offers.id", "flight_offers.user_id"],
            name="match_offer_owner",
        ),
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("watch_id", "deduplication_key"),
        Index("ix_watch_matches_watch_time", "watch_id", "matched_at"),
        Index("ix_watch_matches_source_offer", "source_offer_id"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    watch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_offer_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    deduplication_key: Mapped[str] = mapped_column(String(160), nullable=False)
    offer_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(80))
    environment: Mapped[str | None] = mapped_column(String(16))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="matched")
    match_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    rejection_reason: Mapped[str | None] = mapped_column(String(240))
    matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEventRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_user_time", "user_id", "occurred_at"),)

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )


class OutboxEventRecord(UuidPrimaryKeyMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        Index("ix_outbox_events_ready", "published_at", "available_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic: Mapped[str] = mapped_column(String(120), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


OWNED_RECORD_TYPES = (
    UserSessionRecord,
    TravelerProfileRecord,
    UserTravelPreferenceRecord,
    ChatThreadRecord,
    ChatMessageRecord,
    AgentCheckpointRecord,
    FlightSearchRecord,
    FlightOfferRecord,
    FlightDiscoveryRecord,
    FlightSearchAttemptRecord,
    BookingQuoteRecord,
    BookingIntentRecord,
    BookingRecord,
    BookingEventRecord,
    BookingOperationRecord,
    FlightWatchRecord,
    WatchRunRecord,
    WatchMatchRecord,
    PurchaseMandateRecord,
    WatchHoldRecord,
    WatchNotificationRecord,
    AuditEventRecord,
    OutboxEventRecord,
)
