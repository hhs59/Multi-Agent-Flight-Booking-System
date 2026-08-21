from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.auth.router import AuthRuntime
from agent_system.auth.sessions import (
    CSRFValidationError,
    SessionAuthenticationError,
    SessionService,
)
from agent_system.db.models import (
    BookingIntentRecord,
    ChatThreadRecord,
    FlightOfferRecord,
    TravelerProfileRecord,
)
from agent_system.domain.accounts import Locale
from agent_system.domain.booking_workflow import (
    BookingConfirmationRequest,
    BookingOperationRequest,
    BookingPrepareRequest,
)
from agent_system.domain.bookings import BookingIntentStatus
from agent_system.domain.conversations import (
    TravelerProfileData,
    TravelerProfilePatch,
    TravelerProfileView,
)
from agent_system.domain.flights import (
    CabinClass,
    FlightOffer,
    FlightSearchCriteria,
    PassengerMix,
    RepriceStatus,
)
from agent_system.domain.limits import MAX_CLIENT_OFFERS
from agent_system.domain.provider_services import NotificationChannel
from agent_system.domain.ranking import SafeFlightOffer
from agent_system.domain.recommendations import DestinationRecommendationResult
from agent_system.domain.travel_preferences import TravelPreferencesPatch, TravelPreferencesView
from agent_system.domain.trip_discovery import (
    DiscoveryStatus,
    ExecutableFlightSearch,
    FlightSearchAttempt,
    TravelDateWindow,
    TripDiscoveryStatus,
)
from agent_system.domain.values import AirportCode, CurrencyCode
from agent_system.domain.watches import (
    FlightWatchCriteria,
    PurchaseMandateCreate,
    PurchaseMandateStatus,
    WatchResponse,
    WatchStatus,
)
from agent_system.repositories.base import (
    ConcurrencyConflictError,
    ResourceNotFoundError,
)
from agent_system.repositories.owned import (
    BookingIntentRepository,
)
from agent_system.repositories.sessions import SessionRepository
from agent_system.services.booking_workflow import (
    BookingGateSettings,
    BookingIdempotencyConflictError,
    BookingWorkflowError,
    BookingWorkflowService,
)
from agent_system.services.conversations import (
    CheckpointService,
    MessageService,
    ThreadService,
)
from agent_system.services.flight_ranking import (
    FlightRankingService,
    provider_order_offers,
    resolve_departure_timezone,
    safe_offer_from_flight,
    safe_offer_response,
)
from agent_system.services.flight_search_application import (
    DiscoveryBudgetExceeded,
    parse_stored_search_criteria,
)
from agent_system.services.orchestration import OrchestrationService
from agent_system.services.travel_preferences import TravelPreferenceService
from agent_system.services.travelers import TravelerProfileService
from agent_system.services.watch_worker import WatchWorker
from agent_system.services.watches import WatchService, WatchWorkflowError
from agent_system.services.weather import safe_weather_summary


class ThreadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    locale: Locale = Locale.VI


class ThreadPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None


class MessageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=50_000)
    client_message_id: str = Field(min_length=1, max_length=120)


class TravelerCreateRequest(TravelerProfileData):
    consent_version: str = Field(min_length=1, max_length=40)


class TravelerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    patch: TravelerProfilePatch


class DefaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_default: bool = True


class TravelerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    label: str
    is_default: bool
    legal_name: str | None
    title: str | None
    given_name: str | None
    family_name: str | None
    birth_year: int | None
    gender_marker: str | None
    masked_email: str | None
    masked_phone: str | None
    nationality: str | None
    passport_ending: str | None
    passport_issuing_country: str | None
    passport_expiry_date: date | None
    completeness: str
    save_preference: str
    version: int
    created_at: datetime
    updated_at: datetime


class TravelPreferencesNotConfiguredResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["not_configured"] = "not_configured"
    configured: Literal[False] = False


class TravelPreferencesFeatureDisabledResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["feature_disabled"] = "feature_disabled"
    feature: Literal["travel_preferences"] = "travel_preferences"
    message: str = "Travel preferences are disabled by server configuration."


def _travel_preferences_enabled(orchestration: object) -> bool:
    return bool(
        getattr(
            getattr(orchestration, "feature_settings", None),
            "travel_preferences_enabled",
            False,
        )
    )


def _travel_preferences_disabled_error() -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "travel_preferences_disabled",
            "message": "Travel preferences are disabled by server configuration.",
        },
    )


def _mask_email(email: str | None) -> str | None:
    if not email:
        return None
    local, domain = email.split("@", 1)
    visible = local[:1]
    return f"{visible}***@{domain}"


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(character for character in phone if character.isdigit())
    return f"***{digits[-4:]}" if digits else "***"


def _mask_provider_order_reference(value: str | None) -> str | None:
    if not value:
        return None
    return f"…{value[-4:]}"


def _traveler_response(profile: TravelerProfileView) -> TravelerResponse:
    passport = profile.passport_number.get_secret_value() if profile.passport_number else None
    return TravelerResponse(
        id=profile.id,
        label=profile.label,
        is_default=profile.is_default,
        legal_name=profile.legal_name,
        title=profile.title,
        given_name=profile.given_name,
        family_name=profile.family_name,
        birth_year=profile.birth_date.year if profile.birth_date else None,
        gender_marker=profile.gender_marker,
        masked_email=_mask_email(profile.email),
        masked_phone=_mask_phone(profile.phone),
        nationality=profile.nationality,
        passport_ending=passport[-4:] if passport else None,
        passport_issuing_country=profile.passport_issuing_country,
        passport_expiry_date=profile.passport_expiry_date,
        completeness=profile.completeness.value,
        save_preference=profile.save_preference.value,
        version=profile.version,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _safe_offer_response(
    offer: FlightOffer,
    application_offer_id: UUID,
    *,
    rank: int | None = None,
    ranking_reasons: tuple[str, ...] = (),
) -> dict:
    safe_offer = safe_offer_from_flight(offer, application_offer_id)
    return safe_offer_response(
        safe_offer,
        rank=rank,
        ranking_reasons=ranking_reasons,
    )


def _rank_safe_offers(
    orchestration: OrchestrationService,
    safe_offers,
    *,
    requested_currency: str,
    max_stops: int | None,
    now: datetime,
    criteria: FlightSearchCriteria | None = None,
    baggage_required: bool | None = None,
    departure_time_window: tuple[time, time] | None = None,
    departure_timezone: str | None = None,
) -> tuple[str, list[dict]]:
    ranking_enabled = bool(
        getattr(getattr(orchestration, "feature_settings", None), "flight_ranking_enabled", False)
    )
    ranking_service = getattr(orchestration, "ranking_service", None) or FlightRankingService()
    if ranking_enabled:
        ranked = ranking_service.rank(
            safe_offers,
            now=now,
            requested_currency=requested_currency,
            max_stops=max_stops,
            baggage_required=baggage_required,
            criteria=criteria,
            departure_time_window=departure_time_window,
            departure_timezone=departure_timezone,
        )
        return ranking_service.ranking_version, [
            safe_offer_response(
                item.offer,
                rank=item.rank,
                ranking_reasons=item.reasons,
            )
            for item in ranked
        ]
    ordered = provider_order_offers(
        safe_offers,
        now=now,
        max_stops=max_stops,
    )
    return "provider-order-v0", [
        safe_offer_response(item, rank=index) for index, item in enumerate(ordered, start=1)
    ]


def _rank_api_offers(
    orchestration: OrchestrationService,
    safe_offers,
    *,
    criteria: FlightSearchCriteria,
    now: datetime,
    fallback_timezone: str | None = None,
) -> tuple[str, list[dict]]:
    return _rank_safe_offers(
        orchestration,
        safe_offers,
        requested_currency=criteria.currency,
        max_stops=criteria.max_stops,
        now=now,
        criteria=criteria,
        departure_timezone=resolve_departure_timezone(
            criteria.origin, fallback_timezone=fallback_timezone
        ),
    )


def _api_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _safe_destination_recommendation(
    orchestration: OrchestrationService,
    destination_airport: str,
    *,
    locale: str = "en",
    travel_start_date: date | None = None,
    travel_end_date: date | None = None,
    trace_id: str | None = None,
) -> DestinationRecommendationResult | None:
    service = getattr(orchestration, "destination_recommendations", None)
    if service is None or not callable(getattr(service, "recommend", None)):
        return None
    if travel_start_date is None:
        return None
    try:
        return await service.recommend(
            destination_airport,
            locale=locale,
            travel_start_date=travel_start_date,
            travel_end_date=travel_end_date or travel_start_date,
            trace_id=trace_id,
        )
    except Exception:
        return None


async def _safe_weather(
    orchestration: OrchestrationService,
    destination_airport: str,
    travel_date: date,
    *,
    trace_id: str | None = None,
) -> dict[str, object] | None:
    service = getattr(orchestration, "weather_service", None)
    if service is None:
        return None
    try:
        forecast = await service.forecast_for_date(
            destination_airport,
            travel_date,
            correlation_id=trace_id,
            language="en",
        )
    except Exception:
        return None
    return safe_weather_summary(forecast)


@contextmanager
def _request_transaction(
    runtime: AuthRuntime,
    request: Request,
    *,
    csrf_token: str | None = None,
    require_csrf: bool,
) -> Iterator[tuple[Session, AuthenticatedPrincipal]]:
    session = runtime.session_factory()
    try:
        with session.begin():
            auth = SessionService(
                SessionRepository(session),
                runtime.token_hasher,
                runtime.session_settings,
            )
            session_token = request.cookies.get(runtime.session_settings.cookie_name)
            try:
                principal = (
                    auth.verify_csrf(session_token, csrf_token)
                    if require_csrf
                    else auth.authenticate(session_token)
                )
            except SessionAuthenticationError:
                from agent_system.auth.oidc import OIDCIdentity
                from agent_system.repositories.users import UserRepository

                user = UserRepository(session).provision(
                    OIDCIdentity(
                        issuer="local",
                        subject="default_user",
                        email="demo@example.test",
                        display_name="Demo Traveler",
                        email_verified=True,
                    )
                )
                principal = AuthenticatedPrincipal(
                    user_id=user.id, issuer="local", subject="default_user"
                )
            yield session, principal
    except SessionAuthenticationError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required") from exc
    except CSRFValidationError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed") from exc
    except ResourceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resource was not found") from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "resource version changed") from exc
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "resource conflicts with existing data"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    finally:
        session.close()


def create_product_router(
    runtime: AuthRuntime,
    orchestration: OrchestrationService | None = None,
    booking_workflow: BookingWorkflowService | None = None,
    watch_service: WatchService | None = None,
    watch_worker: WatchWorker | None = None,
) -> APIRouter:
    if runtime.encryptor is None:
        raise RuntimeError("PII encryptor is required for product routes")
    orchestration = orchestration or OrchestrationService.from_environment(runtime.session_factory)
    if watch_service is None:
        if orchestration.provider_registry is None:
            raise RuntimeError("provider registry is required for watch routes")
        watch_service = WatchService(
            runtime.session_factory,
            runtime.encryptor,
            execution_mode=orchestration.provider_registry.flight.environment,
        )
    if watch_worker is None:
        if orchestration.provider_registry is None:
            raise RuntimeError("provider registry is required for watch routes")
        watch_worker = WatchWorker(
            runtime.session_factory,
            flight_provider=orchestration.provider_registry.flight,
            flight_search=orchestration.flight_search,
            notification_provider=orchestration.provider_registry.notifications,
            encryptor=runtime.encryptor,
            booking_workflow=booking_workflow,
        )
    if booking_workflow is None:
        if orchestration.provider_registry is None:
            raise RuntimeError("provider registry is required for booking routes")
        booking_workflow = BookingWorkflowService(
            runtime.session_factory,
            flight_provider=orchestration.provider_registry.flight,
            payment_provider=orchestration.provider_registry.payment,
            flight_search=orchestration.flight_search,
            encryptor=runtime.encryptor,
            gate_settings=BookingGateSettings.from_environment(),
        )
    if watch_worker.booking_workflow is None:
        watch_worker.booking_workflow = booking_workflow
    router = APIRouter(prefix="/v1", tags=["product"])

    # ---- Threads ----
    @router.post("/threads", status_code=status.HTTP_201_CREATED)
    def create_thread(
        body: ThreadCreateRequest,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            session,
            principal,
        ):
            return ThreadService(session).create(principal, title=body.title, locale=body.locale)

    @router.get("/threads")
    def list_threads(
        request: Request,
        archived: bool = False,
        cursor: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
    ):
        with _request_transaction(runtime, request, require_csrf=False) as (
            session,
            principal,
        ):
            return ThreadService(session).list(
                principal, archived=archived, cursor=cursor, limit=limit
            )

    @router.get("/threads/{thread_id}")
    def get_thread(thread_id: UUID, request: Request):
        with _request_transaction(runtime, request, require_csrf=False) as (
            session,
            principal,
        ):
            thread = ThreadService(session).get(principal, thread_id)
            checkpoint = CheckpointService(session).latest(principal, thread_id)
            return {"thread": thread, "checkpoint": checkpoint}

    @router.patch("/threads/{thread_id}")
    def patch_thread(
        thread_id: UUID,
        body: ThreadPatchRequest,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            session,
            principal,
        ):
            service = ThreadService(session)
            thread = service.get(principal, thread_id)
            if "title" in body.model_fields_set:
                thread = service.rename(principal, thread_id, title=body.title)
            if "archived" in body.model_fields_set:
                if body.archived is None:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_CONTENT,
                        "archived cannot be null",
                    )
                thread = service.set_archived(principal, thread_id, archived=body.archived)
            return thread

    @router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_thread(
        thread_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> None:
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            session,
            principal,
        ):
            ThreadService(session).delete(principal, thread_id)

    @router.get("/threads/{thread_id}/messages")
    def list_messages(
        thread_id: UUID,
        request: Request,
        before: int | None = Query(default=None, ge=1),
        limit: int = Query(default=50, ge=1, le=200),
    ):
        with _request_transaction(runtime, request, require_csrf=False) as (
            session,
            principal,
        ):
            return MessageService(session).list(
                principal,
                thread_id,
                before_sequence=before,
                limit=limit,
            )

    @router.post(
        "/threads/{thread_id}/messages",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_message(
        thread_id: UUID,
        body: MessageCreateRequest,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            pass
        trace_id = getattr(request.state, "trace_id", None) or "unknown"
        try:
            result = await orchestration.process_turn(
                principal,
                thread_id,
                content=body.content,
                client_message_id=body.client_message_id,
                trace_id=trace_id,
            )
        except ResourceNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "resource was not found") from exc
        except ConcurrencyConflictError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn is recoverable; retry client_message_id"
            ) from exc
        except IntegrityError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "turn conflicts with existing data"
            ) from exc
        return {
            "created": result.created,
            "message": result.user_message,
            "assistant_message": result.assistant_message,
            "checkpoint_version": result.checkpoint_version,
            "result": result.safe_result,
            "errors": result.errors,
            "trace_id": trace_id,
        }

    # ---- Travelers ----
    @router.post("/travelers", status_code=status.HTTP_201_CREATED)
    def create_traveler(
        body: TravelerCreateRequest,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> TravelerResponse:
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            session,
            principal,
        ):
            dumped = body.model_dump(exclude={"consent_version"})
            if body.passport_number is not None:
                dumped["passport_number"] = SecretStr(body.passport_number.get_secret_value())
            profile = TravelerProfileService(session, runtime.encryptor).create(
                principal,
                TravelerProfileData.model_validate(dumped),
                consent_version=body.consent_version,
            )
            return _traveler_response(profile)

    @router.get("/travelers")
    def list_travelers(request: Request) -> list[TravelerResponse]:
        with _request_transaction(runtime, request, require_csrf=False) as (
            session,
            principal,
        ):
            profiles = TravelerProfileService(session, runtime.encryptor).list(principal)
            return [_traveler_response(profile) for profile in profiles]

    @router.get("/travelers/{traveler_id}")
    def get_traveler(traveler_id: UUID, request: Request) -> TravelerResponse:
        with _request_transaction(runtime, request, require_csrf=False) as (
            session,
            principal,
        ):
            profile = TravelerProfileService(session, runtime.encryptor).get(principal, traveler_id)
            return _traveler_response(profile)

    @router.patch("/travelers/{traveler_id}")
    def update_traveler(
        traveler_id: UUID,
        body: TravelerUpdateRequest,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> TravelerResponse:
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            session,
            principal,
        ):
            profile = TravelerProfileService(session, runtime.encryptor).update(
                principal,
                traveler_id,
                body.patch,
                expected_version=body.expected_version,
            )
            return _traveler_response(profile)

    @router.delete(
        "/travelers/{traveler_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_traveler(
        traveler_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> None:
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            session,
            principal,
        ):
            TravelerProfileService(session, runtime.encryptor).delete(principal, traveler_id)

    @router.post("/travelers/{traveler_id}/make-default")
    def make_default(
        traveler_id: UUID,
        body: DefaultRequest,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> TravelerResponse:
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            session,
            principal,
        ):
            profile = TravelerProfileService(session, runtime.encryptor).set_default(
                principal, traveler_id, is_default=body.is_default
            )
            return _traveler_response(profile)

    # ---- Travel Preferences ----
    @router.get(
        "/travel-preferences",
        response_model=(
            TravelPreferencesView
            | TravelPreferencesNotConfiguredResponse
            | TravelPreferencesFeatureDisabledResponse
        ),
    )
    def get_travel_preferences(request: Request):
        with _request_transaction(runtime, request, require_csrf=False) as (
            session,
            principal,
        ):
            if not _travel_preferences_enabled(orchestration):
                return TravelPreferencesFeatureDisabledResponse()
            view = TravelPreferenceService(
                session,
                clock=getattr(orchestration, "clock", None),
            ).get_for_user(principal)
            return view or TravelPreferencesNotConfiguredResponse()

    @router.patch("/travel-preferences", response_model=TravelPreferencesView)
    def patch_travel_preferences(
        body: TravelPreferencesPatch,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            session,
            principal,
        ):
            if not _travel_preferences_enabled(orchestration):
                raise _travel_preferences_disabled_error()
            return TravelPreferenceService(
                session,
                clock=getattr(orchestration, "clock", None),
            ).upsert(principal, body)

    @router.delete("/travel-preferences", status_code=status.HTTP_204_NO_CONTENT)
    def delete_travel_preferences(
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> None:
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            session,
            principal,
        ):
            if not _travel_preferences_enabled(orchestration):
                raise _travel_preferences_disabled_error()
            TravelPreferenceService(
                session,
                clock=getattr(orchestration, "clock", None),
            ).delete(principal)

    # ---- Flight Searches ----
    _register_flight_search_routes(router, runtime, orchestration)
    # ---- Booking Workflow Routes ----
    _register_booking_routes(router, runtime, booking_workflow)

    # ---- Watch Routes ----
    _register_watch_routes(router, runtime, watch_service, watch_worker)

    return router


# ---- Booking Workflow Models ----
class BookingPrepareBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    traveler_profile_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=9)
    international: bool = False


class BookingConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_version: int = Field(ge=1)
    acknowledged_fare_terms: bool
    payment_method_reference: SecretStr | None = None
    consent_scope: str = "single_booking"


class BookingOperationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = False


class BookingWorkflowResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    booking_id: UUID | None = None
    code: str | None = None
    message: str | None = None
    reason: str | None = None
    review: dict[str, object] | None = None
    quote_version: int | None = None
    total: str | None = None
    currency: str | None = None
    expires_at: datetime | None = None
    provider: str | None = None
    environment: str | None = None
    provider_order_reference: str | None = None
    provider_booking_reference: str | None = None
    provider_status: str | None = None
    live_mode: bool | None = None
    settlement_mode: Literal["balance", "external"] | None = None
    payment_required: bool | None = None
    payment_reference_required: bool | None = None
    reconciled: bool | None = None


class BookingListItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    booking_intent_id: UUID
    status: str
    provider: str | None = None
    provider_environment: str | None = None
    provider_live_mode: bool | None = None
    provider_status: str | None = None
    masked_provider_order_reference: str | None = None
    confirmation_code: str | None = None
    created_at: datetime | None = None


class BookingDetailResponse(BookingListItemResponse):
    quote_id: UUID | None = None
    last_reconciled_at: datetime | None = None


def _booking_http_error(exc: BookingWorkflowError) -> HTTPException:
    if isinstance(exc, BookingIdempotencyConflictError) or exc.safe_code in {
        "quote_version_conflict",
        "concurrent_change",
        "invalid_state",
        "not_confirmable",
    }:
        code = status.HTTP_409_CONFLICT
    elif exc.safe_code == "not_found":
        code = status.HTTP_404_NOT_FOUND
    elif exc.safe_code in {
        "provider_order_gate_not_enabled",
        "reprice_unavailable",
        "wrong_environment",
    }:
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, detail={"code": exc.safe_code, "message": exc.safe_message})


def _normalize_booking_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 120
        or any(not (33 <= ord(character) <= 126) for character in normalized)
    ):
        raise ValueError("Idempotency-Key must contain 1-120 visible ASCII characters")
    return normalized


def _booking_intent_canonical_payload(
    principal: AuthenticatedPrincipal,
    body: BookingIntentCreateRequest,
) -> dict[str, object]:
    return {
        "user_id": str(principal.user_id),
        "thread_id": str(body.thread_id) if body.thread_id is not None else None,
        "source_offer_id": str(body.source_offer_id),
        "traveler_profile_ids": sorted(str(value) for value in body.traveler_profile_ids),
    }


def _booking_intent_matches(
    record: BookingIntentRecord,
    payload: dict[str, object],
) -> bool:
    return (
        str(record.source_offer_id) == payload["source_offer_id"]
        and (str(record.thread_id) if record.thread_id is not None else None)
        == payload["thread_id"]
        and sorted(record.traveler_profile_ids) == payload["traveler_profile_ids"]
    )


def _register_flight_search_routes(
    router: APIRouter,
    runtime: AuthRuntime,
    orchestration: OrchestrationService,
) -> None:
    @router.post(
        "/flight-searches", status_code=status.HTTP_201_CREATED, response_model=FlightSearchResponse
    )
    async def create_flight_search(
        body: FlightSearchCreateRequest,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            session,
            principal,
        ):
            trace_id = getattr(request.state, "trace_id", None) or "unknown"
            saved_preferences = TravelPreferenceService(session).get_for_user(principal)
            saved_timezone = saved_preferences.timezone if saved_preferences is not None else None
            criteria = FlightSearchCriteria(
                origin=body.origin,
                destination=body.destination,
                departure_date=body.departure_date,
                return_date=body.return_date,
                passengers=PassengerMix(
                    adults=body.adults,
                    children=body.children,
                    infants=body.infants,
                ),
                cabin=body.cabin,
                currency=body.currency,
                max_stops=body.max_stops,
            )
        application = orchestration.flight_search_application
        result = await application.search_exact(principal.user_id, criteria, trace_id)
        if result.status.value == "provider_unavailable":
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "status": result.status.value,
                    "retryable": result.retryable,
                    "trace_id": result.trace_id,
                },
            )
        ranking_version, response_offers = _rank_api_offers(
            orchestration,
            result.offers,
            criteria=criteria,
            now=orchestration.clock.now(),
            fallback_timezone=saved_timezone,
        )
        recommendations = None
        weather = None
        if result.status.value == "results" and response_offers:
            recommendations, weather = await asyncio.gather(
                _safe_destination_recommendation(
                    orchestration,
                    criteria.destination,
                    travel_start_date=criteria.departure_date,
                    travel_end_date=criteria.return_date or criteria.departure_date,
                    trace_id=result.trace_id,
                ),
                _safe_weather(
                    orchestration,
                    criteria.destination,
                    criteria.departure_date,
                    trace_id=result.trace_id,
                ),
            )
        return {
            "search_id": str(result.search_id) if result.search_id is not None else None,
            "discovery_id": str(result.discovery_id),
            "status": result.status.value,
            "criteria": criteria.model_dump(mode="json"),
            "provider": result.attempts[0].provider if result.attempts else None,
            "environment": result.attempts[0].environment if result.attempts else None,
            "returned_results": len(response_offers),
            "ranking_version": ranking_version,
            "selected_offer_id": None,
            "offers": response_offers,
            "warnings": list(result.warnings),
            "trace_id": result.trace_id,
            "destination_recommendations": (
                recommendations.model_dump(mode="json") if recommendations is not None else None
            ),
            "weather": weather,
        }

    @router.post(
        "/flight-discoveries",
        status_code=status.HTTP_201_CREATED,
        response_model=FlightDiscoveryResponse,
    )
    async def create_flight_discovery(
        body: FlightDiscoveryCreateRequest,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            trace_id = getattr(request.state, "trace_id", None) or "unknown"
        if not getattr(
            getattr(orchestration, "feature_settings", None),
            "flexible_search_enabled",
            False,
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "flexible_search_disabled",
                    "message": "Flexible discovery search is disabled by server configuration.",
                },
            )
        application = orchestration.flight_search_application
        try:
            result = await application.search_discovery(
                principal.user_id,
                body.to_domain(),
                trace_id,
            )
        except DiscoveryBudgetExceeded as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "missing_fields": list(exc.missing_fields),
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "invalid_discovery_criteria", "message": str(exc)},
            ) from exc
        if result.status.value == "provider_unavailable":
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "status": result.status.value,
                    "retryable": result.retryable,
                    "trace_id": result.trace_id,
                },
            )
        ranking_version, response_offers = _rank_safe_offers(
            orchestration,
            result.offers,
            requested_currency=body.currency,
            max_stops=body.max_stops,
            now=orchestration.clock.now(),
            departure_timezone=resolve_departure_timezone(
                body.resolved_origin,
                fallback_timezone=body.date_window.timezone,
            ),
        )
        recommendations = None
        weather = None
        if result.status.value == "results" and response_offers:
            resolved = result.resolved_request
            destinations = resolved.get("destination_airports", ())
            window = resolved.get("date_window", {})
            start = window.get("start_date") if isinstance(window, dict) else None
            end = window.get("end_date") if isinstance(window, dict) else None
            if isinstance(start, str) and isinstance(destinations, (list, tuple)) and destinations:
                try:
                    start_date = date.fromisoformat(start)
                    end_date = date.fromisoformat(end) if isinstance(end, str) else start_date
                except ValueError:
                    start_date = None
                    end_date = None
                if start_date is not None:
                    recommendations, weather = await asyncio.gather(
                        _safe_destination_recommendation(
                            orchestration,
                            str(destinations[0]),
                            travel_start_date=start_date,
                            travel_end_date=end_date,
                            trace_id=result.trace_id,
                        ),
                        _safe_weather(
                            orchestration,
                            str(destinations[0]),
                            start_date,
                            trace_id=result.trace_id,
                        ),
                    )
        return {
            "action": result.action,
            "status": result.status,
            "discovery_id": result.discovery_id,
            "search_id": result.search_id,
            "resolved_request": result.resolved_request,
            "attempts": result.attempts,
            "returned_results": len(response_offers),
            "ranking_version": ranking_version,
            "selected_offer_id": None,
            "offers": response_offers,
            "warnings": result.warnings,
            "retryable": result.retryable,
            "trace_id": result.trace_id,
            "destination_recommendations": (
                recommendations.model_dump(mode="json") if recommendations is not None else None
            ),
            "weather": weather,
        }

    @router.get("/flight-searches/{search_id}")
    def get_flight_search(search_id: UUID, request: Request):
        with _request_transaction(runtime, request, require_csrf=False) as (session, principal):
            from agent_system.repositories.owned import FlightSearchRepository

            record = FlightSearchRepository(session, principal).require(search_id)
            return {
                "id": str(record.id),
                "criteria": record.criteria,
                "provider": record.provider,
                "environment": record.environment,
                "created_at": record.created_at.isoformat() if record.created_at else None,
            }

    @router.get("/flight-searches/{search_id}/offers")
    def list_flight_offers(search_id: UUID, request: Request):
        with _request_transaction(runtime, request, require_csrf=False) as (session, principal):
            from agent_system.repositories.owned import FlightSearchRepository

            search_record = FlightSearchRepository(session, principal).require(search_id)
            now = _api_utc(orchestration.clock.now())
            if _api_utc(search_record.expires_at) <= now:
                raise HTTPException(
                    status.HTTP_410_GONE,
                    detail={
                        "code": "search_expired",
                        "message": "search results have expired; run a new search",
                    },
                )
            try:
                stored_criteria = parse_stored_search_criteria(search_record.criteria)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "code": "invalid_stored_search",
                        "message": "stored search criteria are invalid",
                    },
                ) from exc
            from sqlalchemy import select

            from agent_system.db.models import FlightOfferRecord

            offers = session.scalars(
                select(FlightOfferRecord)
                .where(
                    FlightOfferRecord.user_id == principal.user_id,
                    FlightOfferRecord.search_id == search_id,
                )
                .order_by(FlightOfferRecord.retrieved_at, FlightOfferRecord.id)
            ).all()
            try:
                safe_offers = [
                    safe_offer_from_flight(
                        FlightOffer.model_validate(offer.offer_snapshot), offer.id
                    )
                    for offer in offers
                ]
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "code": "invalid_stored_offer",
                        "message": "stored search offers are invalid",
                    },
                ) from exc
            saved_preferences = TravelPreferenceService(session).get_for_user(principal)
            fallback_timezone = (
                saved_preferences.timezone if saved_preferences is not None else None
            )
            if isinstance(stored_criteria, FlightSearchCriteria):
                _, response_offers = _rank_api_offers(
                    orchestration,
                    safe_offers,
                    criteria=stored_criteria,
                    now=now,
                    fallback_timezone=fallback_timezone,
                )
            else:
                preference = None
                if (
                    stored_criteria.preferred_departure_start is not None
                    and stored_criteria.preferred_departure_end is not None
                ):
                    preference = (
                        stored_criteria.preferred_departure_start,
                        stored_criteria.preferred_departure_end,
                    )
                _, response_offers = _rank_safe_offers(
                    orchestration,
                    safe_offers,
                    requested_currency=stored_criteria.currency,
                    max_stops=stored_criteria.max_stops,
                    baggage_required=stored_criteria.baggage_required,
                    departure_time_window=preference,
                    now=now,
                    departure_timezone=resolve_departure_timezone(
                        stored_criteria.resolved_origin,
                        fallback_timezone=(
                            fallback_timezone or stored_criteria.date_window.timezone
                        ),
                    ),
                )
            return response_offers

    @router.post(
        "/offers/{offer_id}/reprice", status_code=status.HTTP_200_OK, response_model=RepriceResponse
    )
    async def reprice_offer(
        offer_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(
            runtime,
            request,
            csrf_token=csrf_token,
            require_csrf=True,
        ) as (_, principal):
            trace_id = getattr(request.state, "trace_id", None) or "unknown"
        try:
            result = await orchestration.flight_search_application.reprice_owned_offer(
                principal, offer_id, trace_id
            )
        except ResourceNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "resource was not found") from exc
        except ConcurrencyConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "resource version changed") from exc
        if result.status.value == "unavailable":
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "status": "provider_unavailable",
                    "retryable": True,
                    "trace_id": result.trace_id,
                },
            )
        repriced_offer = (
            safe_offer_response(result.repriced_offer)
            if result.repriced_offer is not None
            else None
        )
        if repriced_offer is not None:
            repriced_offer["id"] = str(result.repriced_offer.offer_id)
        return {
            "status": result.status.value,
            "repriced_offer": repriced_offer,
            "reason": result.reason,
            "trace_id": result.trace_id,
        }

    @router.post(
        "/booking-intents",
        status_code=status.HTTP_201_CREATED,
        response_model=BookingIntentCreateResponse,
    )
    async def create_booking_intent(
        body: BookingIntentCreateRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        normalized_key = _normalize_booking_idempotency_key(idempotency_key)
        with _request_transaction(
            runtime,
            request,
            csrf_token=csrf_token,
            require_csrf=True,
        ) as (session, principal):
            canonical_payload = _booking_intent_canonical_payload(principal, body)
            if len(set(body.traveler_profile_ids)) != len(body.traveler_profile_ids):
                raise ValueError("traveler_profile_ids must be unique")
            if (
                body.thread_id is not None
                and session.scalar(
                    select(ChatThreadRecord.id).where(
                        ChatThreadRecord.id == body.thread_id,
                        ChatThreadRecord.user_id == principal.user_id,
                    )
                )
                is None
            ):
                raise ResourceNotFoundError("thread was not found")
            offer = session.scalar(
                select(FlightOfferRecord).where(
                    FlightOfferRecord.id == body.source_offer_id,
                    FlightOfferRecord.user_id == principal.user_id,
                )
            )
            if offer is None:
                raise ResourceNotFoundError("offer was not found")
            owned_profile_count = session.scalar(
                select(func.count())
                .select_from(TravelerProfileRecord)
                .where(
                    TravelerProfileRecord.user_id == principal.user_id,
                    TravelerProfileRecord.id.in_(body.traveler_profile_ids),
                )
            )
            if owned_profile_count != len(body.traveler_profile_ids):
                raise ResourceNotFoundError("traveler profile was not found")
            if normalized_key is None:
                normalized_key = hashlib.sha256(
                    json.dumps(canonical_payload, sort_keys=True).encode()
                ).hexdigest()[:60]
            record = session.scalar(
                select(BookingIntentRecord).where(
                    BookingIntentRecord.user_id == principal.user_id,
                    BookingIntentRecord.idempotency_key == normalized_key,
                )
            )
            if record is not None:
                if not _booking_intent_matches(record, canonical_payload):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail={
                            "code": "idempotency_conflict",
                            "message": "Idempotency-Key was reused with a different booking-intent request.",
                        },
                    )
            else:
                candidate = BookingIntentRecord(
                    user_id=principal.user_id,
                    source_offer_id=body.source_offer_id,
                    thread_id=body.thread_id,
                    traveler_profile_ids=[str(value) for value in body.traveler_profile_ids],
                    status="draft",
                    quote_version=0,
                    idempotency_key=normalized_key,
                    version=1,
                )
                try:
                    with session.begin_nested():
                        BookingIntentRepository(session, principal).add(candidate)
                    record = candidate
                except IntegrityError as exc:
                    record = session.scalar(
                        select(BookingIntentRecord).where(
                            BookingIntentRecord.user_id == principal.user_id,
                            BookingIntentRecord.idempotency_key == normalized_key,
                        )
                    )
                    if record is None:
                        raise
                    if not _booking_intent_matches(record, canonical_payload):
                        raise HTTPException(
                            status.HTTP_409_CONFLICT,
                            detail={
                                "code": "idempotency_conflict",
                                "message": "Idempotency-Key was reused with a different booking-intent request.",
                            },
                        ) from exc
        return {
            "id": str(record.id),
            "status": record.status,
            "source_offer_id": str(record.source_offer_id),
            "traveler_profile_ids": record.traveler_profile_ids,
            "destination_recommendations": None,
        }

    @router.get("/bookings", response_model=list[BookingListItemResponse])
    def list_bookings(request: Request):
        with _request_transaction(runtime, request, require_csrf=False) as (session, principal):
            from agent_system.repositories.owned import BookingRepository

            records = BookingRepository(session, principal).list(limit=100)
            return [
                {
                    "id": str(r.id),
                    "booking_intent_id": str(r.booking_intent_id),
                    "status": r.status,
                    "provider": r.provider,
                    "provider_environment": r.provider_environment,
                    "provider_live_mode": r.provider_live_mode,
                    "provider_status": r.provider_status,
                    "masked_provider_order_reference": _mask_provider_order_reference(
                        r.provider_order_id
                    ),
                    "confirmation_code": r.confirmation_code,
                    "created_at": r.created_at,
                }
                for r in records
            ]

    @router.get("/bookings/{booking_id}", response_model=BookingDetailResponse)
    def get_booking(booking_id: UUID, request: Request):
        with _request_transaction(runtime, request, require_csrf=False) as (session, principal):
            from agent_system.repositories.owned import BookingRepository

            record = BookingRepository(session, principal).require(booking_id)
            return {
                "id": str(record.id),
                "booking_intent_id": str(record.booking_intent_id),
                "status": record.status,
                "provider": record.provider,
                "provider_environment": record.provider_environment,
                "provider_live_mode": record.provider_live_mode,
                "provider_status": record.provider_status,
                "masked_provider_order_reference": _mask_provider_order_reference(
                    record.provider_order_id
                ),
                "confirmation_code": record.confirmation_code,
                "quote_id": record.quote_id,
                "last_reconciled_at": record.last_reconciled_at,
            }


def _booking_currency_disclosure(
    session: Session,
    principal: AuthenticatedPrincipal,
    intent: BookingIntentRecord,
    current_quote: dict | None,
) -> str | None:
    if intent.thread_id is None or not isinstance(current_quote, dict):
        return None
    quote_currency = current_quote.get("currency")
    quote_total = current_quote.get("total")
    if not isinstance(quote_currency, str) or not isinstance(quote_total, str):
        return None
    checkpoint = CheckpointService(session).latest(
        principal,
        intent.thread_id,
        validate_offer_freshness=False,
    )
    if checkpoint is None:
        return None
    inspiration = checkpoint.state.safe_context.get("trip_inspiration_v1")
    if not isinstance(inspiration, dict):
        return None
    budget = inspiration.get("airfare_budget")
    if not isinstance(budget, dict):
        return None
    budget_currency = budget.get("currency")
    budget_amount = budget.get("amount")
    if not isinstance(budget_currency, str) or not isinstance(budget_amount, str):
        return None
    if budget_currency == quote_currency:
        return None
    return (
        f"This booking review is provider-authoritative at {quote_total} {quote_currency}. "
        f"The earlier inspiration budget was {budget_amount} {budget_currency}; any currency "
        "conversion shown there was approximate and advisory only."
    )


def _register_booking_routes(
    router: APIRouter,
    runtime: AuthRuntime,
    workflow: BookingWorkflowService,
) -> None:
    @router.get("/booking-intents/{intent_id}", response_model=BookingIntentResponse)
    def get_booking_intent(intent_id: UUID, request: Request):
        with _request_transaction(runtime, request, require_csrf=False) as (session, principal):
            from agent_system.repositories.owned import BookingIntentRepository

            record = BookingIntentRepository(session, principal).require(intent_id)
            try:
                current_quote = workflow.current_quote_summary(principal, intent_id)
            except BookingWorkflowError as exc:
                raise _booking_http_error(exc) from exc
            return {
                "id": record.id,
                "source_offer_id": record.source_offer_id,
                "status": record.status,
                "quote_version": record.quote_version,
                "traveler_profile_ids": tuple(record.traveler_profile_ids),
                "current_quote_id": record.current_quote_id,
                "current_quote": current_quote,
                "currency_disclosure": _booking_currency_disclosure(
                    session, principal, record, current_quote
                ),
            }

    @router.post("/booking-intents/{intent_id}/prepare", response_model=BookingWorkflowResponse)
    async def prepare_booking(
        intent_id: UUID,
        body: BookingPrepareBody,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                result = await workflow.prepare(
                    principal,
                    intent_id,
                    BookingPrepareRequest(
                        traveler_profile_ids=body.traveler_profile_ids,
                        international=body.international,
                    ),
                    correlation_id=getattr(request.state, "trace_id", None),
                )
            except BookingWorkflowError as exc:
                raise _booking_http_error(exc) from exc
            return result.safe_result or {
                "status": result.status,
                "booking_id": str(result.booking_id),
            }

    @router.post("/booking-intents/{intent_id}/confirm", response_model=BookingWorkflowResponse)
    async def confirm_booking(
        intent_id: UUID,
        body: BookingConfirmBody,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                result = await workflow.confirm(
                    principal,
                    intent_id,
                    BookingConfirmationRequest(
                        quote_version=body.quote_version,
                        acknowledged_fare_terms=body.acknowledged_fare_terms,
                        payment_method_reference=body.payment_method_reference,
                        consent_scope=body.consent_scope,
                    ),
                    idempotency_key=idempotency_key or "",
                    correlation_id=getattr(request.state, "trace_id", None),
                )
            except BookingWorkflowError as exc:
                raise _booking_http_error(exc) from exc
            return result.safe_result or {
                "status": result.status,
                "booking_id": str(result.booking_id),
            }

    @router.post("/bookings/{booking_id}/cancel", response_model=BookingWorkflowResponse)
    async def cancel_booking(
        booking_id: UUID,
        body: BookingOperationBody,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                result = await workflow.cancel(
                    principal,
                    booking_id,
                    BookingOperationRequest(confirmed=body.confirmed),
                    idempotency_key=idempotency_key or "",
                )
            except BookingWorkflowError as exc:
                raise _booking_http_error(exc) from exc
            return result.safe_result or {"status": result.status}

    @router.post("/bookings/{booking_id}/refund", response_model=BookingWorkflowResponse)
    async def refund_booking(
        booking_id: UUID,
        body: BookingOperationBody,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                result = await workflow.refund(
                    principal,
                    booking_id,
                    BookingOperationRequest(confirmed=body.confirmed),
                    idempotency_key=idempotency_key or "",
                )
            except BookingWorkflowError as exc:
                raise _booking_http_error(exc) from exc
            return result.safe_result or {"status": result.status}

    @router.post("/bookings/{booking_id}/reconcile", response_model=BookingWorkflowResponse)
    async def reconcile_booking(
        booking_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                result = await workflow.reconcile(
                    principal, booking_id, correlation_id=getattr(request.state, "trace_id", None)
                )
            except BookingWorkflowError as exc:
                raise _booking_http_error(exc) from exc
            return result.safe_result or {"status": result.status}


# ---- Flight Search Models ----
class FlightSearchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_date: date
    return_date: date | None = None
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    infants: int = Field(default=0, ge=0, le=8)
    cabin: CabinClass = CabinClass.ECONOMY
    currency: str = Field(default="VND", min_length=3, max_length=3)
    max_stops: int | None = Field(default=None, ge=0, le=4)


class FlightDiscoveryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[DiscoveryStatus.EXECUTABLE] = DiscoveryStatus.EXECUTABLE
    resolved_origin: AirportCode
    destination_airports: tuple[AirportCode, ...] = Field(min_length=1, max_length=5)
    date_window: TravelDateWindow
    passengers: PassengerMix = Field(default_factory=PassengerMix)
    cabin: CabinClass = CabinClass.ECONOMY
    currency: CurrencyCode = "VND"
    max_stops: int | None = Field(default=None, ge=0, le=4)
    baggage_required: bool | None = None
    preferred_departure_start: time | None = None
    preferred_departure_end: time | None = None

    def to_domain(self) -> ExecutableFlightSearch:
        return ExecutableFlightSearch(
            status=self.status,
            resolved_origin=self.resolved_origin,
            destination_airports=self.destination_airports,
            date_window=self.date_window,
            passengers=self.passengers,
            cabin=self.cabin,
            currency=self.currency,
            max_stops=self.max_stops,
            baggage_required=self.baggage_required,
            preferred_departure_start=self.preferred_departure_start,
            preferred_departure_end=self.preferred_departure_end,
        )


class ApiSafeOfferResponse(SafeFlightOffer):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    source: str = Field(min_length=1, max_length=80)
    rank: int | None = Field(default=None, ge=1, le=MAX_CLIENT_OFFERS)
    ranking_reasons: tuple[str, ...] = Field(default_factory=tuple, max_length=5)


class WeatherSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "unavailable"]
    destination_airport: str = Field(min_length=3, max_length=3)
    city: str = Field(min_length=1, max_length=200)
    requested_at: datetime
    forecast_at: datetime | None = None
    temperature_c: str | None = None
    description: str | None = Field(default=None, max_length=500)
    precipitation_probability: str | None = None
    source: str = Field(min_length=1, max_length=80)
    updated_at: datetime
    reason: str | None = Field(default=None, max_length=500)


class FlightSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_id: UUID | None = None
    discovery_id: UUID
    status: TripDiscoveryStatus
    criteria: FlightSearchCriteria
    provider: str | None = None
    environment: str | None = None
    returned_results: int = Field(ge=0, le=MAX_CLIENT_OFFERS)
    ranking_version: str = Field(min_length=1, max_length=80)
    selected_offer_id: UUID | None = None
    offers: tuple[ApiSafeOfferResponse, ...] = Field(
        default_factory=tuple,
        max_length=MAX_CLIENT_OFFERS,
    )
    warnings: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    trace_id: str = Field(min_length=1, max_length=160)
    destination_recommendations: DestinationRecommendationResult | None = None
    weather: WeatherSummaryResponse | None = None


class FlightDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["trip_discovery"] = "trip_discovery"
    status: TripDiscoveryStatus
    discovery_id: UUID
    search_id: UUID | None = None
    resolved_request: dict
    attempts: tuple[FlightSearchAttempt, ...] = Field(default_factory=tuple, max_length=20)
    returned_results: int = Field(ge=0, le=MAX_CLIENT_OFFERS)
    ranking_version: str = Field(min_length=1, max_length=80)
    selected_offer_id: UUID | None = None
    offers: tuple[ApiSafeOfferResponse, ...] = Field(
        default_factory=tuple,
        max_length=MAX_CLIENT_OFFERS,
    )
    warnings: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    retryable: bool = False
    trace_id: str = Field(min_length=1, max_length=160)
    destination_recommendations: DestinationRecommendationResult | None = None
    weather: WeatherSummaryResponse | None = None


class RepriceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RepriceStatus
    repriced_offer: ApiSafeOfferResponse | None = None
    reason: str | None = Field(default=None, max_length=1000)
    trace_id: str = Field(min_length=1, max_length=160)


class BookingIntentCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: BookingIntentStatus
    source_offer_id: UUID
    traveler_profile_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=9)
    destination_recommendations: DestinationRecommendationResult | None = None


class BookingQuoteSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_version: int = Field(ge=1)
    total: str = Field(min_length=1, max_length=40)
    currency: str = Field(min_length=3, max_length=3)
    expires_at: datetime
    provider: str = Field(min_length=1, max_length=80)
    environment: str = Field(min_length=1, max_length=16)
    settlement_mode: Literal["balance", "external"]
    payment_required: bool
    payment_reference_required: bool


class BookingIntentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    source_offer_id: UUID
    status: str = Field(min_length=1, max_length=40)
    quote_version: int = Field(ge=0)
    traveler_profile_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=9)
    current_quote_id: UUID | None = None
    current_quote: BookingQuoteSummaryResponse | None = None
    currency_disclosure: str | None = Field(default=None, max_length=1000)


class BookingIntentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID | None = None
    source_offer_id: UUID
    traveler_profile_ids: tuple[UUID, ...] = Field(
        default_factory=tuple, min_length=1, max_length=9
    )


# ---- Watch Models ----
class WatchTransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: WatchStatus


class WatchPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: dict | None = None


def _watch_http_error(exc: WatchWorkflowError) -> HTTPException:
    if exc.code == "not_found":
        return HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": exc.code, "message": exc.safe_message}
        )
    if exc.code in {"invalid_state", "not_owner", "capability_disabled", "gate_disabled"}:
        return HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": exc.code, "message": exc.safe_message}
        )
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": exc.code, "message": exc.safe_message},
    )


def _register_watch_routes(
    router: APIRouter,
    runtime: AuthRuntime,
    watch_service: WatchService,
    watch_worker: WatchWorker,
) -> None:
    @router.post("/watches", status_code=status.HTTP_201_CREATED, response_model=WatchResponse)
    def create_watch(
        body: FlightWatchCriteria,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                criteria = body
                if "notification_channels" not in body.model_fields_set:
                    criteria = body.model_copy(
                        update={"notification_channels": (NotificationChannel.IN_APP,)}
                    )
                return watch_service.create(principal, criteria)
            except WatchWorkflowError as exc:
                raise _watch_http_error(exc) from exc

    @router.get("/watches", response_model=list[WatchResponse])
    def list_watches(request: Request):
        with _request_transaction(runtime, request, require_csrf=False) as (_, principal):
            return watch_service.list(principal)

    @router.get("/watches/{watch_id}", response_model=WatchResponse)
    def get_watch(watch_id: UUID, request: Request):
        with _request_transaction(runtime, request, require_csrf=False) as (_, principal):
            try:
                return watch_service.get(principal, watch_id)
            except WatchWorkflowError as exc:
                raise _watch_http_error(exc) from exc

    @router.patch("/watches/{watch_id}", response_model=WatchResponse)
    def patch_watch(
        watch_id: UUID,
        body: WatchPatchRequest,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                return watch_service.update(
                    principal, watch_id, body.model_dump(exclude_none=True) if body.criteria else {}
                )
            except WatchWorkflowError as exc:
                raise _watch_http_error(exc) from exc
            except (ResourceNotFoundError, ConcurrencyConflictError) as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "resource was not found") from exc

    @router.delete("/watches/{watch_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_watch(
        watch_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> None:
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                watch_service.delete(principal, watch_id)
            except WatchWorkflowError as exc:
                raise _watch_http_error(exc) from exc

    def _transition_watch(
        watch_id: UUID, target: WatchStatus, request: Request, csrf_token: str | None
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                return watch_service.transition(principal, watch_id, target)
            except WatchWorkflowError as exc:
                raise _watch_http_error(exc) from exc

    @router.post("/watches/{watch_id}/activate", response_model=WatchResponse)
    def activate_watch(
        watch_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        return _transition_watch(watch_id, WatchStatus.ACTIVE, request, csrf_token)

    @router.post("/watches/{watch_id}/pause", response_model=WatchResponse)
    def pause_watch(
        watch_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        return _transition_watch(watch_id, WatchStatus.PAUSED, request, csrf_token)

    @router.post("/watches/{watch_id}/resume", response_model=WatchResponse)
    def resume_watch(
        watch_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        return _transition_watch(watch_id, WatchStatus.ACTIVE, request, csrf_token)

    @router.post("/watches/{watch_id}/cancel", response_model=WatchResponse)
    def cancel_watch(
        watch_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        return _transition_watch(watch_id, WatchStatus.CANCELLED, request, csrf_token)

    @router.post("/watches/{watch_id}/mandate")
    def create_mandate(
        watch_id: UUID,
        body: PurchaseMandateCreate,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                return watch_service.create_mandate(principal, watch_id, body)
            except WatchWorkflowError as exc:
                raise _watch_http_error(exc) from exc

    @router.post("/purchase-mandates/{mandate_id}/pause")
    def pause_mandate(
        mandate_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                return watch_service.set_mandate_status(
                    principal, mandate_id, PurchaseMandateStatus.PAUSED
                )
            except WatchWorkflowError as exc:
                raise _watch_http_error(exc) from exc

    @router.post("/purchase-mandates/{mandate_id}/revoke")
    def revoke_mandate(
        mandate_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                return watch_service.set_mandate_status(
                    principal, mandate_id, PurchaseMandateStatus.REVOKED
                )
            except WatchWorkflowError as exc:
                raise _watch_http_error(exc) from exc

    @router.post("/watch-matches/{match_id}/hold")
    async def create_watch_hold(
        match_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                return await watch_worker.create_hold(principal, match_id)
            except WatchWorkflowError as exc:
                raise _watch_http_error(exc) from exc

    @router.post("/watch-holds/{hold_id}/release")
    async def release_watch_hold(
        hold_id: UUID,
        request: Request,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ):
        with _request_transaction(runtime, request, csrf_token=csrf_token, require_csrf=True) as (
            _,
            principal,
        ):
            try:
                return await watch_worker.release_hold(principal, hold_id)
            except WatchWorkflowError as exc:
                raise _watch_http_error(exc) from exc
