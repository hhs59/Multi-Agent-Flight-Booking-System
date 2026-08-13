from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import (
    BookingEventRecord,
    BookingIntentRecord,
    BookingOperationRecord,
    BookingQuoteRecord,
    BookingRecord,
    FlightOfferRecord,
)
from agent_system.domain.booking_workflow import (
    BookingConfirmationRequest,
    BookingOperation,
    BookingOperationRequest,
    BookingPrepareRequest,
    BookingWorkflowStatus,
    ensure_booking_transition,
)
from agent_system.domain.bookings import BookingQuote, TravelerSnapshot
from agent_system.domain.flights import PassengerType, RepriceStatus
from agent_system.domain.provider_services import PaymentResult, PaymentStatus
from agent_system.domain.values import ExecutionMode, Money
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.contracts import FlightProvider, PaymentProvider
from agent_system.providers.errors import (
    ProviderError,
    ProviderTimeoutError,
)
from agent_system.repositories.events import OutboxRepository
from agent_system.security.encryption import FieldEncryptor
from agent_system.services.flight_search import FlightSearchService
from agent_system.services.travelers import TravelerProfileService, TravelerValidationError


class BookingWorkflowError(RuntimeError):
    def __init__(self, safe_code: str, message: str) -> None:
        super().__init__(message)
        self.safe_code = safe_code
        self.safe_message = message


class BookingIdempotencyConflictError(BookingWorkflowError):
    pass


@dataclass(frozen=True)
class BookingWorkflowResult:
    intent_id: UUID
    booking_id: UUID | None
    status: str
    quote_version: int
    operation_id: UUID | None = None
    safe_result: dict[str, Any] | None = None


@dataclass(frozen=True)
class BookingGateSettings:
    order_enabled: bool = False
    production_approved: bool = False
    sandbox_contract_verified: bool = False
    point_of_sale_approved: bool = False
    ticketing_agreement_confirmed: bool = False
    payment_collection_approved: bool = False
    cancellation_owner_assigned: bool = False

    @classmethod
    def from_environment(cls) -> BookingGateSettings:
        def flag(name: str) -> bool:
            return os.getenv(name, "false").lower() in {"1", "true", "yes"}

        return cls(
            order_enabled=flag("BOOKING_ORDER_ENABLED"),
            production_approved=flag("BOOKING_PRODUCTION_APPROVED"),
            sandbox_contract_verified=flag("BOOKING_SANDBOX_CONTRACT_VERIFIED"),
            point_of_sale_approved=flag("BOOKING_POINT_OF_SALE_APPROVED"),
            ticketing_agreement_confirmed=flag("BOOKING_TICKETING_AGREEMENT_CONFIRMED"),
            payment_collection_approved=flag("BOOKING_PAYMENT_COLLECTION_APPROVED"),
            cancellation_owner_assigned=flag("BOOKING_CANCELLATION_OWNER_ASSIGNED"),
        )

    def allows_order(self, environment: ExecutionMode) -> bool:
        if not self.order_enabled:
            return False
        if environment is ExecutionMode.PRODUCTION:
            return all(
                (
                    self.production_approved,
                    self.sandbox_contract_verified,
                    self.point_of_sale_approved,
                    self.ticketing_agreement_confirmed,
                    self.payment_collection_approved,
                    self.cancellation_owner_assigned,
                )
            )
        return self.sandbox_contract_verified or environment is ExecutionMode.MOCK


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _fingerprint(value: Any) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(rendered.encode()).hexdigest()


def _mask_reference(value: str | None) -> str | None:
    if not value:
        return None
    return f"…{value[-4:]}"


def _safe_provider_metadata(value: Any) -> dict[str, Any]:
    return {
        "provider": value.provider,
        "environment": value.environment.value,
        "is_live": value.is_live,
        "retrieved_at": value.retrieved_at.isoformat(),
        "expires_at": value.expires_at.isoformat() if value.expires_at else None,
    }


class BookingWorkflowService:
    def __init__(
        self,
        session_factory,
        *,
        flight_provider: FlightProvider,
        payment_provider: PaymentProvider,
        flight_search: FlightSearchService,
        encryptor: FieldEncryptor,
        gate_settings: BookingGateSettings | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.flight_provider = flight_provider
        self.payment_provider = payment_provider
        self.flight_search = flight_search
        self.encryptor = encryptor
        self.gates = gate_settings or BookingGateSettings.from_environment()
        self.clock = clock or SystemClock()

    def _lock_intent(self, session: Session, principal: AuthenticatedPrincipal, intent_id: UUID):
        intent = session.scalar(
            select(BookingIntentRecord)
            .where(
                BookingIntentRecord.id == intent_id,
                BookingIntentRecord.user_id == principal.user_id,
            )
            .with_for_update()
        )
        if intent is None:
            raise BookingWorkflowError("not_found", "booking intent was not found")
        return intent

    def _ensure_booking(
        self,
        session: Session,
        principal: AuthenticatedPrincipal,
        intent: BookingIntentRecord,
    ) -> BookingRecord:
        booking = session.scalar(
            select(BookingRecord)
            .where(
                BookingRecord.booking_intent_id == intent.id,
                BookingRecord.user_id == principal.user_id,
            )
            .with_for_update()
        )
        if booking is None:
            booking = BookingRecord(
                user_id=principal.user_id,
                booking_intent_id=intent.id,
                status="pending",
                idempotency_key=f"booking:{intent.id}",
            )
            session.add(booking)
            session.flush()
        return booking

    def _event(
        self,
        session: Session,
        principal: AuthenticatedPrincipal,
        booking: BookingRecord,
        *,
        event_type: str,
        from_status: str | None,
        to_status: str | None,
        idempotency_key: str,
        resulting_version: int,
        actor_type: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> BookingEventRecord:
        event = BookingEventRecord(
            user_id=principal.user_id,
            booking_id=booking.id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=principal.user_id if actor_type == "user" else None,
            from_status=from_status,
            to_status=to_status,
            idempotency_key=idempotency_key,
            resulting_version=resulting_version,
            payload=payload or {},
        )
        session.add(event)
        session.flush()
        return event

    def _transition(
        self,
        session: Session,
        principal: AuthenticatedPrincipal,
        intent: BookingIntentRecord,
        booking: BookingRecord,
        target: BookingWorkflowStatus,
        *,
        event_type: str,
        idempotency_key: str,
        actor_type: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> None:
        ensure_booking_transition(intent.status, target)
        previous = intent.status
        intent.status = target.value
        intent.version += 1
        booking.version += 1
        session.flush()
        self._event(
            session,
            principal,
            booking,
            event_type=event_type,
            from_status=previous,
            to_status=target.value,
            idempotency_key=f"{idempotency_key}:{target.value}:{intent.version}",
            resulting_version=intent.version,
            actor_type=actor_type,
            payload=payload,
        )

    def _load_offer(self, session: Session, principal: AuthenticatedPrincipal, intent):
        offer_record = session.scalar(
            select(FlightOfferRecord).where(
                FlightOfferRecord.id == intent.source_offer_id,
                FlightOfferRecord.user_id == principal.user_id,
            )
        )
        if offer_record is None:
            raise BookingWorkflowError("not_found", "selected flight offer was not found")
        if _utc(offer_record.expires_at) <= self.clock.now():
            raise BookingWorkflowError("offer_expired", "selected flight offer has expired")
        return offer_record

    def _current_quote_record(
        self,
        session: Session,
        principal: AuthenticatedPrincipal,
        intent,
    ) -> BookingQuoteRecord | None:
        if intent.current_quote_id is None:
            return None
        return session.scalar(
            select(BookingQuoteRecord).where(
                BookingQuoteRecord.id == intent.current_quote_id,
                BookingQuoteRecord.user_id == principal.user_id,
                BookingQuoteRecord.booking_intent_id == intent.id,
            )
        )

    def _load_quote(self, session: Session, principal: AuthenticatedPrincipal, intent):
        quote_record = self._current_quote_record(session, principal, intent)
        if quote_record is None:
            raise BookingWorkflowError("quote_missing", "booking quote was not found")
        if _utc(quote_record.expires_at) <= self.clock.now():
            raise BookingWorkflowError("quote_expired", "booking quote has expired")
        return quote_record

    def _travelers(
        self,
        session: Session,
        principal: AuthenticatedPrincipal,
        intent_id: UUID,
    ) -> tuple[TravelerSnapshot, ...]:
        service = TravelerProfileService(session, self.encryptor)
        snapshots = service.load_booking_snapshots(principal, intent_id)
        return tuple(
            TravelerSnapshot(
                traveler_profile_id=item.traveler_profile_id,
                legal_name=item.legal_name,
                title=item.title,
                given_name=item.given_name,
                family_name=item.family_name,
                birth_date=item.birth_date,
                gender_marker=item.gender_marker,
                email=item.email,
                phone=item.phone,
                nationality=item.nationality,
                passport_number=item.passport_number,
                passport_issuing_country=item.passport_issuing_country,
                passport_expiry_date=item.passport_expiry_date,
            )
            for item in snapshots
        )

    def _uses_provider_balance(self, quote: BookingQuoteRecord) -> bool:
        capabilities = self.flight_provider.capabilities()
        return bool(
            quote.provider == "duffel"
            and self.flight_provider.name == "duffel"
            and quote.environment == ExecutionMode.SANDBOX.value
            and self.flight_provider.environment is ExecutionMode.SANDBOX
            and capabilities.can_book
            and capabilities.requires_instant_payment
            and getattr(
                getattr(self.flight_provider, "settings", None), "settlement_mode", "balance"
            )
            == "balance"
        )

    def current_quote_summary(
        self,
        principal: AuthenticatedPrincipal,
        intent_id: UUID,
    ) -> dict[str, Any] | None:
        session = self.session_factory()
        try:
            with session.begin():
                intent = self._lock_intent(session, principal, intent_id)
                quote = self._current_quote_record(session, principal, intent)
                if quote is None:
                    return None
                uses_provider_balance = self._uses_provider_balance(quote)
                return {
                    "quote_version": quote.version,
                    "total": str(quote.total_amount),
                    "currency": quote.currency,
                    "expires_at": _utc(quote.expires_at).isoformat(),
                    "provider": quote.provider,
                    "environment": quote.environment,
                    "settlement_mode": "balance" if uses_provider_balance else "external",
                    "payment_required": not uses_provider_balance,
                    "payment_reference_required": not uses_provider_balance,
                }
        finally:
            session.close()

    @staticmethod
    def _review(
        quote: BookingQuoteRecord, travelers: tuple[TravelerSnapshot, ...]
    ) -> dict[str, Any]:
        offer = quote.quote_snapshot
        segments = offer.get("segments", [])
        return {
            "quote_id": str(quote.id),
            "quote_version": quote.version,
            "provider": quote.provider,
            "environment": quote.environment,
            "total": str(quote.total_amount),
            "currency": quote.currency,
            "expires_at": _utc(quote.expires_at).isoformat(),
            "segments": [
                {
                    "origin": item.get("origin"),
                    "destination": item.get("destination"),
                    "departure_at": item.get("departure_at"),
                    "arrival_at": item.get("arrival_at"),
                    "flight_number": item.get("flight_number"),
                }
                for item in segments
            ],
            "traveler_count": len(travelers),
            "traveler_labels": [
                f"Traveler {index + 1} ({item.legal_name[:1]}***)"
                for index, item in enumerate(travelers)
            ],
            "fare_terms": offer.get("fare_conditions", {}),
            "baggage": offer.get("baggage", {}),
        }

    async def prepare(
        self,
        principal: AuthenticatedPrincipal,
        intent_id: UUID,
        request: BookingPrepareRequest,
        *,
        correlation_id: str | None = None,
    ) -> BookingWorkflowResult:
        session = self.session_factory()
        try:
            with session.begin():
                intent = self._lock_intent(session, principal, intent_id)
                booking = self._ensure_booking(session, principal, intent)
                offer_record = self._load_offer(session, principal, intent)
                if not request.traveler_profile_ids:
                    if intent.status != BookingWorkflowStatus.NEEDS_TRAVELERS.value:
                        self._transition(
                            session,
                            principal,
                            intent,
                            booking,
                            BookingWorkflowStatus.NEEDS_TRAVELERS,
                            event_type="needs_travelers",
                            idempotency_key=f"prepare:{intent.id}",
                        )
                    return BookingWorkflowResult(
                        intent.id,
                        booking.id,
                        intent.status,
                        intent.quote_version,
                        safe_result={"status": "needs_travelers"},
                    )
                offer = offer_record.offer_snapshot
                from agent_system.domain.flights import FlightOffer

                typed_offer = FlightOffer.model_validate(offer)
                provider_offer_id = typed_offer.metadata.provider_offer_id
                if (
                    provider_offer_id is None
                    or typed_offer.metadata.provider != self.flight_provider.name
                    or typed_offer.metadata.environment is not self.flight_provider.environment
                ):
                    raise BookingWorkflowError(
                        "wrong_environment", "offer provider environment is not bookable here"
                    )
                if self.flight_provider.name == "duffel" and any(
                    item.passenger_type is not PassengerType.ADULT
                    for item in typed_offer.provider_passengers
                ):
                    raise BookingWorkflowError(
                        "unsupported_passenger_types",
                        "Duffel sandbox orders currently support adults only",
                    )
                provider_required_fields = (
                    ("title", "given_name", "family_name", "gender_marker", "phone")
                    if self.flight_provider.name == "duffel"
                    and typed_offer.metadata.environment is ExecutionMode.SANDBOX
                    else ()
                )
                profile_service = TravelerProfileService(session, self.encryptor)
                try:
                    snapshots = profile_service.snapshot_booking_intent(
                        principal,
                        intent.id,
                        request.traveler_profile_ids,
                        international=request.international,
                        provider_required_fields=provider_required_fields,
                        expected_version=intent.version,
                    )
                except TravelerValidationError as exc:
                    missing = ", ".join(exc.validation.missing_fields[:8])
                    raise BookingWorkflowError(
                        "traveler_incomplete",
                        f"traveler profiles are missing required fields: {missing or 'invalid data'}",
                    ) from exc
                if intent.status not in {
                    BookingWorkflowStatus.DRAFT.value,
                    BookingWorkflowStatus.NEEDS_TRAVELERS.value,
                    BookingWorkflowStatus.AWAITING_CONFIRMATION.value,
                    BookingWorkflowStatus.QUOTE_READY.value,
                    BookingWorkflowStatus.NEEDS_USER_ACTION.value,
                    BookingWorkflowStatus.EXPIRED.value,
                }:
                    raise BookingWorkflowError(
                        "invalid_state", "booking is not ready for repricing"
                    )
                self._transition(
                    session,
                    principal,
                    intent,
                    booking,
                    BookingWorkflowStatus.REPRICING,
                    event_type="repricing_started",
                    idempotency_key=f"prepare:{intent.id}:{intent.version}",
                )
                expected_version = intent.version
                typed_offer_id = provider_offer_id
        finally:
            session.close()

        try:
            repriced = await self.flight_search.reprice(
                typed_offer_id,
                typed_offer,
                correlation_id=correlation_id,
            )
        except ProviderTimeoutError as exc:
            return self._finish_reprice_failure(principal, intent_id, "reprice_timeout", str(exc))
        except ProviderError as exc:
            return self._finish_reprice_failure(
                principal, intent_id, "reprice_unavailable", exc.safe_message
            )

        if repriced.status is RepriceStatus.EXPIRED:
            return self._finish_reprice_failure(
                principal, intent_id, "quote_expired", repriced.reason or "offer expired"
            )
        if repriced.status is RepriceStatus.UNAVAILABLE or repriced.repriced_offer is None:
            return self._finish_reprice_failure(
                principal, intent_id, "reprice_unavailable", repriced.reason or "offer unavailable"
            )

        now = self.clock.now()
        repriced_offer = repriced.repriced_offer
        expiry = repriced_offer.metadata.expires_at or now + timedelta(minutes=5)
        expiry = min(expiry, now + timedelta(minutes=5))
        session = self.session_factory()
        try:
            with session.begin():
                intent = self._lock_intent(session, principal, intent_id)
                booking = self._ensure_booking(session, principal, intent)
                if intent.version != expected_version:
                    raise BookingWorkflowError(
                        "concurrent_change", "booking changed while repricing"
                    )
                quote = BookingQuoteRecord(
                    user_id=principal.user_id,
                    booking_intent_id=intent.id,
                    source_offer_id=intent.source_offer_id,
                    version=intent.quote_version + 1,
                    provider=repriced_offer.metadata.provider,
                    environment=repriced_offer.metadata.environment.value,
                    quote_snapshot=repriced_offer.model_dump(mode="json"),
                    total_amount=repriced_offer.total.amount,
                    currency=repriced_offer.total.currency,
                    expires_at=expiry,
                    created_at=now,
                )
                session.add(quote)
                session.flush()
                intent.current_quote_id = quote.id
                intent.quote_version = quote.version
                booking.quote_id = quote.id
                session.flush()
                self._transition(
                    session,
                    principal,
                    intent,
                    booking,
                    BookingWorkflowStatus.QUOTE_READY,
                    event_type="reprice_completed",
                    idempotency_key=f"prepare:{intent.id}:{quote.version}",
                    payload={
                        "quote_version": quote.version,
                        "price_changed": repriced.status is RepriceStatus.CHANGED,
                    },
                )
                self._transition(
                    session,
                    principal,
                    intent,
                    booking,
                    BookingWorkflowStatus.AWAITING_CONFIRMATION,
                    event_type="confirmation_requested",
                    idempotency_key=f"prepare:{intent.id}:{quote.version}:confirmation",
                )
                travelers = tuple(
                    TravelerSnapshot(
                        traveler_profile_id=item.traveler_profile_id,
                        legal_name=item.legal_name,
                        title=item.title,
                        given_name=item.given_name,
                        family_name=item.family_name,
                        birth_date=item.birth_date,
                        gender_marker=item.gender_marker,
                        email=item.email,
                        phone=item.phone,
                        nationality=item.nationality,
                        passport_number=item.passport_number,
                        passport_issuing_country=item.passport_issuing_country,
                        passport_expiry_date=item.passport_expiry_date,
                    )
                    for item in snapshots
                )
                review = self._review(quote, travelers)
                uses_provider_balance = self._uses_provider_balance(quote)
                return BookingWorkflowResult(
                    intent.id,
                    booking.id,
                    intent.status,
                    quote.version,
                    safe_result={
                        "status": "awaiting_confirmation",
                        "review": review,
                        "quote_version": quote.version,
                        "total": str(quote.total_amount),
                        "currency": quote.currency,
                        "expires_at": _utc(quote.expires_at).isoformat(),
                        "provider": quote.provider,
                        "environment": quote.environment,
                        "settlement_mode": "balance" if uses_provider_balance else "external",
                        "payment_required": not uses_provider_balance,
                        "payment_reference_required": not uses_provider_balance,
                    },
                )
        finally:
            session.close()

    def _finish_reprice_failure(
        self,
        principal: AuthenticatedPrincipal,
        intent_id: UUID,
        code: str,
        detail: str,
    ) -> BookingWorkflowResult:
        session = self.session_factory()
        try:
            with session.begin():
                intent = self._lock_intent(session, principal, intent_id)
                booking = self._ensure_booking(session, principal, intent)
                target = (
                    BookingWorkflowStatus.EXPIRED
                    if code == "quote_expired"
                    else BookingWorkflowStatus.NEEDS_USER_ACTION
                )
                self._transition(
                    session,
                    principal,
                    intent,
                    booking,
                    target,
                    event_type="reprice_failed",
                    idempotency_key=f"reprice-failure:{intent.id}:{intent.version}",
                    payload={"code": code, "detail": detail[:200]},
                )
                return BookingWorkflowResult(
                    intent.id,
                    booking.id,
                    intent.status,
                    intent.quote_version,
                    safe_result={"status": code, "message": detail[:300]},
                )
        finally:
            session.close()

    def _operation_or_create(
        self,
        session: Session,
        principal: AuthenticatedPrincipal,
        *,
        operation: BookingOperation,
        idempotency_key: str,
        request_fingerprint: str,
        intent_id: UUID | None = None,
        booking_id: UUID | None = None,
    ) -> BookingOperationRecord:
        existing = session.scalar(
            select(BookingOperationRecord)
            .where(
                BookingOperationRecord.idempotency_key == idempotency_key,
                BookingOperationRecord.user_id == principal.user_id,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise BookingIdempotencyConflictError(
                    "idempotency_conflict", "Idempotency-Key was reused with a different request"
                )
            return existing
        record = BookingOperationRecord(
            user_id=principal.user_id,
            booking_intent_id=intent_id,
            booking_id=booking_id,
            operation=operation.value,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            status="processing",
            result={},
        )
        session.add(record)
        session.flush()
        return record

    def _operation_result(
        self,
        operation: BookingOperationRecord,
        *,
        status: str,
        result: dict[str, Any],
    ) -> BookingOperationRecord:
        operation.status = status
        operation.result = result
        operation.updated_at = self.clock.now()
        return operation

    async def confirm(
        self,
        principal: AuthenticatedPrincipal,
        intent_id: UUID,
        request: BookingConfirmationRequest,
        *,
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> BookingWorkflowResult:
        if not idempotency_key or len(idempotency_key) > 120:
            raise BookingWorkflowError("idempotency_required", "Idempotency-Key is required")
        reference_value = (
            request.payment_method_reference.get_secret_value()
            if request.payment_method_reference
            else None
        )
        request_fingerprint = _fingerprint(
            {
                "quote_version": request.quote_version,
                "acknowledged_fare_terms": request.acknowledged_fare_terms,
                "payment_reference_hash": hashlib.sha256(reference_value.encode()).hexdigest()
                if reference_value
                else None,
                "consent_scope": request.consent_scope,
            }
        )
        session = self.session_factory()
        try:
            with session.begin():
                intent = self._lock_intent(session, principal, intent_id)
                booking = self._ensure_booking(session, principal, intent)
                operation = self._operation_or_create(
                    session,
                    principal,
                    operation=BookingOperation.CONFIRM,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    intent_id=intent.id,
                    booking_id=booking.id,
                )
                if operation.status != "processing":
                    return BookingWorkflowResult(
                        intent.id,
                        booking.id,
                        intent.status,
                        intent.quote_version,
                        operation.id,
                        operation.result,
                    )
                if not request.acknowledged_fare_terms:
                    raise BookingWorkflowError(
                        "terms_not_acknowledged", "fare and cancellation terms must be acknowledged"
                    )
                if intent.status != BookingWorkflowStatus.AWAITING_CONFIRMATION.value:
                    raise BookingWorkflowError(
                        "not_confirmable", "booking is not awaiting confirmation"
                    )
                quote = self._load_quote(session, principal, intent)
                if quote.version != request.quote_version:
                    raise BookingWorkflowError(
                        "quote_version_conflict", "confirmation must use the current quote version"
                    )
                uses_provider_balance = self._uses_provider_balance(quote)
                if uses_provider_balance and reference_value is not None:
                    raise BookingWorkflowError(
                        "payment_reference_not_allowed",
                        "Duffel balance settlement does not accept a payment reference",
                    )
                if not uses_provider_balance and reference_value is None:
                    raise BookingWorkflowError(
                        "payment_method_required", "a provider payment reference is required"
                    )
                if reference_value is not None:
                    encrypted = self.encryptor.encrypt_text(
                        reference_value,
                        associated_data=self._payment_aad(principal.user_id, booking.id, "method"),
                    )
                    booking.payment_method_reference_encrypted = encrypted.ciphertext
                    booking.payment_reference_key_version = encrypted.key_version
                booking.quote_id = quote.id
                booking.consent_snapshot = {
                    "quote_version": quote.version,
                    "total": str(quote.total_amount),
                    "currency": quote.currency,
                    "acknowledged_fare_terms": True,
                    "consent_scope": request.consent_scope,
                    "settlement_mode": "balance" if uses_provider_balance else "external",
                    "payment_reference_required": not uses_provider_balance,
                }
                booking.confirmed_by_user_at = self.clock.now()
                self._transition(
                    session,
                    principal,
                    intent,
                    booking,
                    BookingWorkflowStatus.CONFIRMED_BY_USER,
                    event_type="user_confirmed",
                    idempotency_key=idempotency_key,
                    actor_type="user",
                    payload={
                        "quote_version": quote.version,
                        "total": str(quote.total_amount),
                        "currency": quote.currency,
                    },
                )
                operation_id = operation.id
        finally:
            session.close()

        return await self._run_confirmation_saga(
            principal,
            intent_id,
            operation_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def _run_confirmation_saga(
        self,
        principal: AuthenticatedPrincipal,
        intent_id: UUID,
        operation_id: UUID,
        *,
        idempotency_key: str,
        correlation_id: str | None,
    ) -> BookingWorkflowResult:
        del correlation_id
        session = self.session_factory()
        try:
            with session.begin():
                intent = self._lock_intent(session, principal, intent_id)
                booking = self._ensure_booking(session, principal, intent)
                operation = session.scalar(
                    select(BookingOperationRecord)
                    .where(
                        BookingOperationRecord.id == operation_id,
                        BookingOperationRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                quote = self._load_quote(session, principal, intent)
                travelers = self._travelers(session, principal, intent.id)
                provider_environment = ExecutionMode(quote.environment)
                uses_provider_balance = self._uses_provider_balance(quote)
                if (
                    not self.gates.allows_order(provider_environment)
                    or not self.flight_provider.capabilities().can_book
                ):
                    result = {
                        "status": "needs_user_action",
                        "reason": "provider_order_gate_not_enabled",
                        "environment": quote.environment,
                        "provider": quote.provider,
                    }
                    self._transition(
                        session,
                        principal,
                        intent,
                        booking,
                        BookingWorkflowStatus.NEEDS_USER_ACTION,
                        event_type="order_gate_blocked",
                        idempotency_key=idempotency_key,
                        payload=result,
                    )
                    self._operation_result(operation, status="needs_user_action", result=result)
                    return BookingWorkflowResult(
                        intent.id,
                        booking.id,
                        intent.status,
                        intent.quote_version,
                        operation.id,
                        result,
                    )
                quote_model = BookingQuote(
                    id=quote.id,
                    user_id=principal.user_id,
                    booking_intent_id=intent.id,
                    source_offer_id=intent.source_offer_id,
                    offer=quote.quote_snapshot,
                    travelers=travelers,
                    created_at=_utc(quote.created_at),
                    expires_at=_utc(quote.expires_at),
                )
                amount = Money(amount=Decimal(str(quote.total_amount)), currency=quote.currency)
                if uses_provider_balance:
                    self._transition(
                        session,
                        principal,
                        intent,
                        booking,
                        BookingWorkflowStatus.CREATING_ORDER,
                        event_type="provider_balance_order_started",
                        idempotency_key=idempotency_key,
                        payload={
                            "provider": quote.provider,
                            "settlement_mode": "balance",
                            "payment_reference_required": False,
                        },
                    )
                else:
                    self._transition(
                        session,
                        principal,
                        intent,
                        booking,
                        BookingWorkflowStatus.PAYMENT_AUTHORIZING,
                        event_type="payment_authorization_started",
                        idempotency_key=idempotency_key,
                    )
                    payment_method = self.encryptor.decrypt_text(
                        booking.payment_method_reference_encrypted,
                        key_version=booking.payment_reference_key_version,
                        associated_data=self._payment_aad(principal.user_id, booking.id, "method"),
                    )
        finally:
            session.close()

        auth_reference: str | None = None
        if not uses_provider_balance:
            auth_result: PaymentResult
            try:
                auth_result = await self.payment_provider.authorize(
                    amount,
                    SecretStr(payment_method),
                    f"{idempotency_key}:authorize",
                )
            except ProviderTimeoutError as exc:
                return self._mark_uncertain(
                    principal, intent_id, operation_id, "payment_authorization_uncertain", str(exc)
                )
            except ProviderError as exc:
                return self._mark_failed(
                    principal,
                    intent_id,
                    operation_id,
                    "payment_authorization_failed",
                    exc.safe_message,
                )
            if auth_result.status is PaymentStatus.REQUIRES_ACTION:
                return self._mark_needs_action(
                    principal, intent_id, operation_id, "payment_requires_action"
                )
            if (
                auth_result.status is not PaymentStatus.AUTHORIZED
                or auth_result.transaction_reference is None
            ):
                return self._mark_failed(
                    principal,
                    intent_id,
                    operation_id,
                    "payment_declined",
                    auth_result.reason_code or "payment declined",
                )

            auth_reference = auth_result.transaction_reference.get_secret_value()
            session = self.session_factory()
            try:
                with session.begin():
                    intent = self._lock_intent(session, principal, intent_id)
                    booking = self._ensure_booking(session, principal, intent)
                    encrypted = self.encryptor.encrypt_text(
                        auth_reference,
                        associated_data=self._payment_aad(
                            principal.user_id, booking.id, "authorization"
                        ),
                    )
                    booking.payment_authorization_reference_encrypted = encrypted.ciphertext
                    booking.payment_reference_key_version = encrypted.key_version
                    self._transition(
                        session,
                        principal,
                        intent,
                        booking,
                        BookingWorkflowStatus.CREATING_ORDER,
                        event_type="payment_authorized",
                        idempotency_key=idempotency_key,
                    )
            finally:
                session.close()

        try:
            order = await self.flight_provider.create_order(
                quote_model,
                travelers,
                f"{idempotency_key}:order",
            )
        except ProviderTimeoutError as exc:
            return self._mark_reconciliation_required(
                principal, intent_id, operation_id, "order_creation_uncertain", str(exc)
            )
        except ProviderError as exc:
            if auth_reference is not None:
                try:
                    await self.payment_provider.cancel(
                        SecretStr(auth_reference),
                        f"{idempotency_key}:authorization-cancel",
                    )
                except ProviderError:
                    return self._mark_reconciliation_required(
                        principal,
                        intent_id,
                        operation_id,
                        "authorization_cancel_uncertain",
                        exc.safe_message,
                    )
            return self._mark_failed(
                principal, intent_id, operation_id, "order_creation_failed", exc.safe_message
            )

        order_id = order.provider_order_id.get_secret_value()
        capture: PaymentResult | None = None
        if not uses_provider_balance:
            assert auth_reference is not None
            try:
                capture = await self.payment_provider.capture(
                    SecretStr(auth_reference),
                    amount,
                    f"{idempotency_key}:capture",
                )
            except ProviderTimeoutError as exc:
                return self._mark_reconciliation_required(
                    principal, intent_id, operation_id, "capture_uncertain", str(exc)
                )
            except ProviderError as exc:
                return self._mark_failed(
                    principal, intent_id, operation_id, "capture_failed", exc.safe_message
                )
            if (
                capture.status is not PaymentStatus.CAPTURED
                or capture.transaction_reference is None
            ):
                return self._mark_reconciliation_required(
                    principal,
                    intent_id,
                    operation_id,
                    "capture_uncertain",
                    capture.reason_code or "payment capture did not complete",
                )

        session = self.session_factory()
        try:
            with session.begin():
                intent = self._lock_intent(session, principal, intent_id)
                booking = self._ensure_booking(session, principal, intent)
                operation = session.scalar(
                    select(BookingOperationRecord)
                    .where(
                        BookingOperationRecord.id == operation_id,
                        BookingOperationRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                booking.provider = order.metadata.provider
                booking.provider_order_id = order_id
                booking.provider_environment = order.metadata.environment.value
                booking.provider_live_mode = order.live_mode
                booking.provider_status = order.provider_status
                booking.confirmation_code = order.booking_reference
                booking.status = "order_created"
                booking.version += 1
                if capture is not None and capture.transaction_reference is not None:
                    captured = self.encryptor.encrypt_text(
                        capture.transaction_reference.get_secret_value(),
                        associated_data=self._payment_aad(
                            principal.user_id, booking.id, "captured"
                        ),
                    )
                    booking.captured_payment_reference_encrypted = captured.ciphertext
                self._transition(
                    session,
                    principal,
                    intent,
                    booking,
                    BookingWorkflowStatus.TICKETING_PENDING,
                    event_type="order_created",
                    idempotency_key=idempotency_key,
                    payload={
                        "provider": order.metadata.provider,
                        "environment": order.metadata.environment.value,
                        "provider_status": order.provider_status,
                        "live_mode": order.live_mode,
                        "settlement_mode": "balance" if uses_provider_balance else "external",
                    },
                )
                result = {
                    "status": "ticketing_pending",
                    "booking_id": str(booking.id),
                    "provider": order.metadata.provider,
                    "environment": order.metadata.environment.value,
                    "provider_order_reference": _mask_reference(order_id),
                    "provider_booking_reference": order.booking_reference,
                    "provider_status": order.provider_status,
                    "live_mode": order.live_mode,
                    "settlement_mode": "balance" if uses_provider_balance else "external",
                    "payment_required": not uses_provider_balance,
                    "payment_reference_required": not uses_provider_balance,
                }
                self._operation_result(operation, status="completed", result=result)
                OutboxRepository(session, principal).enqueue(
                    topic="booking.created",
                    aggregate_type="booking",
                    aggregate_id=booking.id,
                    payload={"booking_id": str(booking.id), "status": "ticketing_pending"},
                    idempotency_key=f"booking-created:{booking.id}:{booking.version}",
                    available_at=self.clock.now(),
                )
                return BookingWorkflowResult(
                    intent.id, booking.id, intent.status, intent.quote_version, operation.id, result
                )
        finally:
            session.close()

    def _mark_failed(self, principal, intent_id, operation_id, code, detail):
        return self._mark_terminal(principal, intent_id, operation_id, "failed", code, detail)

    def _mark_needs_action(self, principal, intent_id, operation_id, code):
        return self._mark_terminal(
            principal, intent_id, operation_id, "needs_user_action", code, code
        )

    def _mark_uncertain(self, principal, intent_id, operation_id, code, detail):
        return self._mark_terminal(
            principal, intent_id, operation_id, "needs_user_action", code, detail
        )

    def _mark_reconciliation_required(self, principal, intent_id, operation_id, code, detail):
        return self._mark_terminal(
            principal, intent_id, operation_id, "needs_reconciliation", code, detail
        )

    def _mark_terminal(self, principal, intent_id, operation_id, status, code, detail):
        session = self.session_factory()
        try:
            with session.begin():
                intent = self._lock_intent(session, principal, intent_id)
                booking = self._ensure_booking(session, principal, intent)
                operation = session.scalar(
                    select(BookingOperationRecord)
                    .where(
                        BookingOperationRecord.id == operation_id,
                        BookingOperationRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                target = (
                    BookingWorkflowStatus.FAILED
                    if status == "failed"
                    else BookingWorkflowStatus.NEEDS_USER_ACTION
                )
                if intent.status != target.value:
                    self._transition(
                        session,
                        principal,
                        intent,
                        booking,
                        target,
                        event_type=code,
                        idempotency_key=f"operation:{operation_id}:{code}",
                        payload={"detail": detail[:300]},
                    )
                if status == "needs_reconciliation":
                    booking.status = "needs_reconciliation"
                    booking.version += 1
                result = {"status": status, "code": code, "message": detail[:300]}
                self._operation_result(operation, status=status, result=result)
                return BookingWorkflowResult(
                    intent.id, booking.id, intent.status, intent.quote_version, operation.id, result
                )
        finally:
            session.close()

    async def reconcile(
        self,
        principal: AuthenticatedPrincipal,
        booking_id: UUID,
        *,
        correlation_id: str | None = None,
    ) -> BookingWorkflowResult:
        del correlation_id
        session = self.session_factory()
        try:
            with session.begin():
                booking = session.scalar(
                    select(BookingRecord)
                    .where(
                        BookingRecord.id == booking_id, BookingRecord.user_id == principal.user_id
                    )
                    .with_for_update()
                )
                if booking is None:
                    raise BookingWorkflowError("not_found", "booking was not found")
                intent = self._lock_intent(session, principal, booking.booking_intent_id)
                provider_order_id = booking.provider_order_id
        finally:
            session.close()
        if not provider_order_id:
            session = self.session_factory()
            try:
                with session.begin():
                    booking = session.scalar(
                        select(BookingRecord)
                        .where(
                            BookingRecord.id == booking_id,
                            BookingRecord.user_id == principal.user_id,
                        )
                        .with_for_update()
                    )
                    if booking is not None:
                        booking.status = "needs_reconciliation"
                        booking.last_reconciled_at = self.clock.now()
                        booking.version += 1
            finally:
                session.close()
            return BookingWorkflowResult(
                intent.id,
                booking_id,
                intent.status,
                intent.quote_version,
                safe_result={
                    "status": "needs_reconciliation",
                    "code": "reconcile_missing_order",
                    "message": "provider order identity is not available; manual provider reconciliation is required",
                },
            )
        try:
            order = await self.flight_provider.get_order(provider_order_id)
        except ProviderError as exc:
            session = self.session_factory()
            try:
                with session.begin():
                    booking = session.scalar(
                        select(BookingRecord)
                        .where(
                            BookingRecord.id == booking_id,
                            BookingRecord.user_id == principal.user_id,
                        )
                        .with_for_update()
                    )
                    if booking is not None:
                        booking.status = "needs_reconciliation"
                        booking.last_reconciled_at = self.clock.now()
                        booking.version += 1
            finally:
                session.close()
            return BookingWorkflowResult(
                intent.id,
                booking_id,
                intent.status,
                intent.quote_version,
                safe_result={
                    "status": "needs_reconciliation",
                    "code": "reconcile_unavailable",
                    "message": exc.safe_message,
                },
            )
        session = self.session_factory()
        try:
            with session.begin():
                booking = session.scalar(
                    select(BookingRecord)
                    .where(
                        BookingRecord.id == booking_id, BookingRecord.user_id == principal.user_id
                    )
                    .with_for_update()
                )
                intent = self._lock_intent(session, principal, booking.booking_intent_id)
                booking.provider_order_id = order.provider_order_id.get_secret_value()
                booking.provider = order.metadata.provider
                booking.provider_environment = order.metadata.environment.value
                booking.provider_live_mode = order.live_mode
                booking.provider_status = order.provider_status
                booking.confirmation_code = order.booking_reference
                booking.status = "order_created"
                booking.last_reconciled_at = self.clock.now()
                booking.version += 1
                if intent.status == BookingWorkflowStatus.NEEDS_USER_ACTION.value:
                    self._transition(
                        session,
                        principal,
                        intent,
                        booking,
                        BookingWorkflowStatus.TICKETING_PENDING,
                        event_type="reconciled_order",
                        idempotency_key=f"reconcile:{booking.id}",
                        payload={"provider": order.metadata.provider},
                    )
                return BookingWorkflowResult(
                    intent.id,
                    booking.id,
                    intent.status,
                    intent.quote_version,
                    safe_result={
                        "status": "ticketing_pending",
                        "booking_id": str(booking.id),
                        "provider": order.metadata.provider,
                        "environment": order.metadata.environment.value,
                        "provider_order_reference": _mask_reference(
                            order.provider_order_id.get_secret_value()
                        ),
                        "provider_booking_reference": order.booking_reference,
                        "provider_status": order.provider_status,
                        "live_mode": order.live_mode,
                        "settlement_mode": "balance"
                        if order.metadata.provider == "duffel"
                        else "external",
                        "reconciled": True,
                    },
                )
        finally:
            session.close()

    async def cancel(
        self,
        principal: AuthenticatedPrincipal,
        booking_id: UUID,
        request: BookingOperationRequest,
        *,
        idempotency_key: str,
    ) -> BookingWorkflowResult:
        return await self._cancel_or_refund(
            principal, booking_id, request, idempotency_key=idempotency_key, refund=False
        )

    async def refund(
        self,
        principal: AuthenticatedPrincipal,
        booking_id: UUID,
        request: BookingOperationRequest,
        *,
        idempotency_key: str,
    ) -> BookingWorkflowResult:
        return await self._cancel_or_refund(
            principal, booking_id, request, idempotency_key=idempotency_key, refund=True
        )

    async def _cancel_or_refund(self, principal, booking_id, request, *, idempotency_key, refund):
        if not request.confirmed:
            raise BookingWorkflowError(
                "confirmation_required", "a separate cancellation/refund confirmation is required"
            )
        operation_kind = BookingOperation.REFUND if refund else BookingOperation.CANCEL
        session = self.session_factory()
        try:
            with session.begin():
                booking = session.scalar(
                    select(BookingRecord)
                    .where(
                        BookingRecord.id == booking_id, BookingRecord.user_id == principal.user_id
                    )
                    .with_for_update()
                )
                if booking is None:
                    raise BookingWorkflowError("not_found", "booking was not found")
                intent = self._lock_intent(session, principal, booking.booking_intent_id)
                operation = self._operation_or_create(
                    session,
                    principal,
                    operation=operation_kind,
                    idempotency_key=idempotency_key,
                    request_fingerprint=_fingerprint(
                        {"booking_id": str(booking_id), "confirmed": True}
                    ),
                    intent_id=intent.id,
                    booking_id=booking.id,
                )
                if operation.status != "processing":
                    return BookingWorkflowResult(
                        intent.id,
                        booking.id,
                        intent.status,
                        intent.quote_version,
                        operation.id,
                        operation.result,
                    )
                if refund:
                    if not self.flight_provider.capabilities().can_refund:
                        result = {"status": "needs_user_action", "code": "refund_not_supported"}
                        self._operation_result(operation, status="needs_user_action", result=result)
                        return BookingWorkflowResult(
                            intent.id,
                            booking.id,
                            intent.status,
                            intent.quote_version,
                            operation.id,
                            result,
                        )
                    captured = self.encryptor.decrypt_text(
                        booking.captured_payment_reference_encrypted,
                        key_version=booking.payment_reference_key_version,
                        associated_data=self._payment_aad(
                            principal.user_id, booking.id, "captured"
                        ),
                    )
                    quote = self._load_quote(session, principal, intent)
                    amount = Money(amount=Decimal(str(quote.total_amount)), currency=quote.currency)
                else:
                    if (
                        not self.flight_provider.capabilities().can_cancel
                        or not booking.provider_order_id
                    ):
                        result = {
                            "status": "needs_user_action",
                            "code": "cancellation_not_supported",
                        }
                        self._operation_result(operation, status="needs_user_action", result=result)
                        return BookingWorkflowResult(
                            intent.id,
                            booking.id,
                            intent.status,
                            intent.quote_version,
                            operation.id,
                            result,
                        )
                    provider_order_id = booking.provider_order_id
                operation_id = operation.id
        finally:
            session.close()
        try:
            if refund:
                payment = await self.payment_provider.refund(
                    SecretStr(captured), amount, f"{idempotency_key}:refund"
                )
                if payment.status is not PaymentStatus.REFUNDED:
                    return self._mark_uncertain(
                        principal,
                        intent.id,
                        operation_id,
                        "refund_uncertain",
                        payment.reason_code or "refund did not complete",
                    )
            else:
                await self.flight_provider.cancel_order(
                    provider_order_id, f"{idempotency_key}:cancel"
                )
        except ProviderTimeoutError as exc:
            return self._mark_uncertain(
                principal, intent.id, operation_id, "operation_uncertain", str(exc)
            )
        except ProviderError as exc:
            return self._mark_failed(
                principal, intent.id, operation_id, "operation_failed", exc.safe_message
            )
        session = self.session_factory()
        try:
            with session.begin():
                booking = session.scalar(
                    select(BookingRecord)
                    .where(
                        BookingRecord.id == booking_id, BookingRecord.user_id == principal.user_id
                    )
                    .with_for_update()
                )
                intent = self._lock_intent(session, principal, booking.booking_intent_id)
                operation = session.scalar(
                    select(BookingOperationRecord)
                    .where(
                        BookingOperationRecord.id == operation_id,
                        BookingOperationRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                target = (
                    BookingWorkflowStatus.CANCELLED
                    if not refund
                    else BookingWorkflowStatus.CANCELLED
                )
                if intent.status != BookingWorkflowStatus.CANCELLING.value and not refund:
                    self._transition(
                        session,
                        principal,
                        intent,
                        booking,
                        BookingWorkflowStatus.CANCELLING,
                        event_type="cancellation_started",
                        idempotency_key=idempotency_key,
                    )
                booking.status = "refunded" if refund else "cancelled"
                booking.version += 1
                if refund:
                    result = {"status": "refunded", "booking_id": str(booking.id)}
                    self._operation_result(operation, status="completed", result=result)
                else:
                    if intent.status != target.value:
                        self._transition(
                            session,
                            principal,
                            intent,
                            booking,
                            target,
                            event_type="cancelled",
                            idempotency_key=idempotency_key,
                        )
                    result = {"status": "cancelled", "booking_id": str(booking.id)}
                    self._operation_result(operation, status="completed", result=result)
                return BookingWorkflowResult(
                    intent.id, booking.id, intent.status, intent.quote_version, operation.id, result
                )
        finally:
            session.close()

    @staticmethod
    def _payment_aad(user_id: UUID, booking_id: UUID, purpose: str) -> bytes:
        return f"booking-payment:{user_id}:{booking_id}:{purpose}:v1".encode()
