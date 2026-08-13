from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import (
    AgentCheckpointRecord,
    BookingIntentRecord,
    BookingRecord,
    ChatMessageRecord,
    FlightOfferRecord,
    FlightSearchRecord,
    FlightWatchRecord,
    TravelerProfileRecord,
)
from agent_system.domain.accounts import ChatRole
from agent_system.domain.conversations import CheckpointState, MessageView
from agent_system.domain.flights import FlightOffer, FlightSearchCriteria
from agent_system.domain.limits import MAX_PRESENTED_OFFERS
from agent_system.domain.optimization import OptimizationPreference
from agent_system.domain.orchestration import (
    AgentCommand,
    ConfirmBookingCommand,
    CreateWatchCommand,
    ManageBookingCommand,
    ManageWatchCommand,
    SearchFlightsCommand,
    SearchInspirationOptionCommand,
    StartBookingCommand,
    UpdateProfileCommand,
)
from agent_system.domain.ranking import SafeFlightOffer
from agent_system.domain.values import Money
from agent_system.llm_providers import (
    LLMProvider,
    LLMSettings,
    build_llm_provider,
)
from agent_system.orchestration_graph import OrchestrationState, build_orchestration_graph
from agent_system.providers.cache import SearchCache, WeatherCache
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.errors import ProviderError
from agent_system.providers.exchange_rates import (
    ExchangeRateProvider,
    build_exchange_rate_provider,
    quantize_currency,
)
from agent_system.providers.location_fixtures import CatalogLocationProvider
from agent_system.providers.registry import ProviderRegistry, build_provider_registry
from agent_system.providers.resilience import ProviderExecutor
from agent_system.providers.settings import ProviderSettings
from agent_system.repositories.base import ConcurrencyConflictError
from agent_system.repositories.conversations import MessageRepository, ThreadRepository
from agent_system.security.messages import sanitize_message_text
from agent_system.security.safe_results import (
    SAFE_RESULT_SCHEMA_VERSION,
    SafeResultError,
    sanitize_safe_result,
    validate_safe_errors,
)
from agent_system.services.conversations import CheckpointService, MessageService
from agent_system.services.date_resolution import TripDiscoverySettings
from agent_system.services.destination_recommendations import (
    DestinationRecommendationService,
)
from agent_system.services.feature_settings import FeatureSettings
from agent_system.services.flight_ranking import (
    FlightRankingService,
    provider_order_offers,
    resolve_departure_timezone,
    safe_offer_response,
)
from agent_system.services.flight_search import FlightSearchService
from agent_system.services.flight_search_application import FlightSearchApplicationService
from agent_system.services.location_resolution import (
    LocationResolutionCache,
    LocationResolutionService,
)
from agent_system.services.travel_preferences import TravelPreferenceService
from agent_system.services.trip_discovery import TripDiscoveryService
from agent_system.services.trip_inspiration import (
    TripInspirationService,
    TripInspirationSettings,
)
from agent_system.services.weather import WeatherService, safe_weather_summary


def _db_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _assistant_key(client_message_id: str) -> str:
    return "assistant:" + hashlib.sha256(client_message_id.encode()).hexdigest()


_PROCESSING_SAFE_RESULT = {"status": "processing"}
_REPLAY_POLL_ATTEMPTS = 300
_REPLAY_POLL_INTERVAL_SECONDS = 0.01

logger = logging.getLogger(__name__)


def _is_processing_replay(record: ChatMessageRecord | None) -> bool:
    return bool(
        record is not None
        and isinstance(record.safe_result, dict)
        and record.safe_result.get("status") == "processing"
    )


def _checkpoint_version_for_assistant(
    session: Session,
    principal: AuthenticatedPrincipal,
    thread_id: UUID,
    assistant_id: UUID,
) -> int:
    return int(
        session.scalar(
            select(AgentCheckpointRecord.version).where(
                AgentCheckpointRecord.user_id == principal.user_id,
                AgentCheckpointRecord.thread_id == thread_id,
                AgentCheckpointRecord.last_message_id == assistant_id,
            )
        )
        or 0
    )


def _message_view(record: ChatMessageRecord) -> MessageView:
    result = None
    if isinstance(record.safe_result, dict):
        try:
            result = sanitize_safe_result(record.safe_result)
        except SafeResultError:
            # The text message remains readable even if a legacy result is invalid.
            result = None
    return MessageView(
        id=record.id,
        user_id=record.user_id,
        thread_id=record.thread_id,
        role=record.role,
        content=record.content,
        sequence=record.sequence,
        client_message_id=record.client_message_id,
        result=result,
        created_at=_db_utc(record.created_at),
    )


def _stored_replay(record: ChatMessageRecord) -> tuple[dict[str, Any], tuple[str, ...]]:
    if record.safe_result_schema_version not in {None, SAFE_RESULT_SCHEMA_VERSION}:
        result = {"status": "replay_unavailable"}
    elif isinstance(record.safe_result, dict):
        try:
            result = sanitize_safe_result(record.safe_result)
        except SafeResultError:
            result = {"status": "replay_unavailable"}
    else:
        result = {"status": "replay_unavailable"}
    errors = (
        tuple(error for error in record.safe_errors if isinstance(error, str))
        if isinstance(record.safe_errors, list)
        else ()
    )
    return dict(result), errors


def _last_action_projection(
    result: dict[str, Any],
    *,
    selected_offer_id: str | UUID | None = None,
    booking_intent_id: str | UUID | None = None,
    watch_draft_id: str | UUID | None = None,
) -> dict[str, Any] | None:
    action = result.get("action")
    status = result.get("status")
    if not isinstance(action, str) or not isinstance(status, str):
        return None
    projection: dict[str, Any] = {"action": action, "status": status}
    for key, value in (
        ("search_id", result.get("search_id")),
        ("offer_id", result.get("offer_id")),
        ("selected_offer_id", selected_offer_id or result.get("selected_offer_id")),
        ("booking_intent_id", booking_intent_id or result.get("booking_intent_id")),
        ("watch_draft_id", watch_draft_id or result.get("watch_draft_id")),
    ):
        if value is not None:
            try:
                projection[key] = str(UUID(str(value)))
            except (TypeError, ValueError):
                continue
    return projection


def _checkpoint_context(
    previous: dict[str, Any],
    graph_context: Any,
    result: dict[str, Any],
    *,
    selected_offer_id: str | UUID | None = None,
    booking_intent_id: str | UUID | None = None,
    watch_draft_id: str | UUID | None = None,
) -> dict[str, Any]:
    context = dict(previous)
    context.pop("last_result", None)
    context.pop("traveler_profiles", None)
    if isinstance(graph_context, dict):
        for key in (
            "trip_discovery_v1",
            "presented_offers_v1",
            "trip_inspiration_v1",
            "travel_preferences_v1",
        ):
            if key in graph_context:
                context[key] = graph_context[key]
    last_action = _last_action_projection(
        result,
        selected_offer_id=selected_offer_id,
        booking_intent_id=booking_intent_id,
        watch_draft_id=watch_draft_id,
    )
    if last_action is not None:
        context["last_action_v1"] = last_action
    return context


class SafeActionError(RuntimeError):
    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


class OrchestrationActionExecutor:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        flight_search: FlightSearchService,
        destination_recommendations: DestinationRecommendationService | None = None,
        flight_search_application: FlightSearchApplicationService | None = None,
        weather_service: WeatherService | None = None,
        *,
        clock: Clock | None = None,
        ranking_service: FlightRankingService | None = None,
        ranking_enabled: bool = False,
        exchange_rates: ExchangeRateProvider | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.flight_search = flight_search
        self.flight_search_application = (
            flight_search_application
            or FlightSearchApplicationService(session_factory, flight_search, clock=clock)
        )
        self.clock = clock or SystemClock()
        self.ranking_service = ranking_service or FlightRankingService()
        self.ranking_enabled = ranking_enabled
        self.exchange_rates = exchange_rates
        self.destination_recommendations = (
            destination_recommendations or DestinationRecommendationService()
        )
        self.weather_service = weather_service

    async def execute(
        self,
        command: AgentCommand,
        state: OrchestrationState,
    ) -> dict[str, Any]:
        try:
            if isinstance(command, SearchInspirationOptionCommand):
                return await self._search_inspiration_option(command, state)
            if isinstance(command, SearchFlightsCommand):
                return await self._search(command, state)
            if isinstance(command, StartBookingCommand):
                return await self._start_booking(command, state)
            if isinstance(command, ConfirmBookingCommand):
                return self._confirm_booking(command, state)
            if isinstance(command, ManageBookingCommand):
                return self._manage_booking(command, state)
            if isinstance(command, CreateWatchCommand):
                return self._create_watch(command, state)
            if isinstance(command, ManageWatchCommand):
                return self._manage_watch(command, state)
            if isinstance(command, UpdateProfileCommand):
                return self._profile(command, state)
            return {"status": "clarification_required"}
        except ProviderError as exc:
            raise SafeActionError("provider_unavailable") from exc

    @staticmethod
    def _identity(state: OrchestrationState) -> tuple[UUID, UUID]:
        return UUID(state["authenticated_user_id"]), UUID(state["thread_id"])

    async def _search_inspiration_option(
        self,
        command: SearchInspirationOptionCommand,
        state: OrchestrationState,
    ) -> dict[str, Any]:
        user_id, _ = self._identity(state)
        raw_context = state.get("safe_context", {})
        projection = (
            raw_context.get("trip_inspiration_v1") if isinstance(raw_context, dict) else None
        )
        mapping = (
            next(
                (
                    item
                    for item in projection.get("options", ())
                    if isinstance(item, dict) and item.get("rank") == command.option.rank
                ),
                None,
            )
            if isinstance(projection, dict)
            else None
        )
        if mapping is None:
            return {
                "action": "search_flights",
                "status": "clarification_required",
                "message_vi": "Không tìm thấy phương án cảm hứng này. Vui lòng yêu cầu tìm lại điểm đến.",
                "message_en": "That inspiration option is not present. Please ask me to search destination ideas again.",
            }
        try:
            search_id = UUID(str(mapping["search_id"]))
            offer_id = UUID(str(mapping["application_offer_id"]))
            option_expiry = _db_utc(datetime.fromisoformat(str(mapping["expires_at"])))
        except (KeyError, TypeError, ValueError):
            return {
                "action": "search_flights",
                "status": "clarification_required",
                "message_vi": "Phương án cảm hứng không còn hợp lệ. Vui lòng tìm lại các điểm đến.",
                "message_en": "That inspiration option is not valid anymore. Please search destination ideas again.",
            }
        if option_expiry <= self.clock.now():
            raise SafeActionError("offer_expired")
        session = self.session_factory()
        try:
            with session.begin():
                search = session.scalar(
                    select(FlightSearchRecord).where(
                        FlightSearchRecord.id == search_id,
                        FlightSearchRecord.user_id == user_id,
                    )
                )
                offer = session.scalar(
                    select(FlightOfferRecord).where(
                        FlightOfferRecord.id == offer_id,
                        FlightOfferRecord.search_id == search_id,
                        FlightOfferRecord.user_id == user_id,
                    )
                )
                if search is None or offer is None:
                    return {
                        "action": "search_flights",
                        "status": "clarification_required",
                        "message_vi": "Phương án cảm hứng không thuộc tài khoản này hoặc đã bị thay đổi.",
                        "message_en": "That inspiration option is not owned by this account or has changed.",
                    }
                if (
                    _db_utc(search.expires_at) <= self.clock.now()
                    or _db_utc(offer.expires_at) <= self.clock.now()
                ):
                    raise SafeActionError("offer_expired")
                criteria_payload = dict(search.criteria)
        finally:
            session.close()
        try:
            criteria = FlightSearchCriteria.model_validate(criteria_payload)
        except (TypeError, ValueError) as exc:
            raise SafeActionError("action_failed") from exc
        allowed_airports = mapping.get("airport_codes")
        if isinstance(allowed_airports, list) and criteria.destination not in allowed_airports:
            raise SafeActionError("action_failed")
        airfare_budget = self._checkpoint_money(projection, "airfare_budget")
        optimization = self._checkpoint_optimization(projection)
        return await self._search(
            SearchFlightsCommand(
                origin=criteria.origin,
                destination=criteria.destination,
                departure_date=criteria.departure_date,
                return_date=criteria.return_date,
                passengers=criteria.passengers,
                cabin=criteria.cabin,
                currency=criteria.currency,
                max_stops=criteria.max_stops,
                baggage_required=criteria.baggage_required,
                preferred_departure_start=criteria.preferred_departure_start,
                preferred_departure_end=criteria.preferred_departure_end,
            ),
            state,
            airfare_budget=airfare_budget,
            optimization=optimization,
        )

    @staticmethod
    def _checkpoint_money(projection: object, key: str) -> Money | None:
        if not isinstance(projection, dict):
            return None
        value = projection.get(key)
        if value is None:
            return None
        try:
            return Money.model_validate(value)
        except (TypeError, ValueError):
            logger.warning(
                "ignoring invalid inspiration money constraint",
                extra={"constraint": key},
            )
            return None

    @staticmethod
    def _checkpoint_optimization(projection: object) -> OptimizationPreference | None:
        if not isinstance(projection, dict):
            return None
        value = projection.get("optimization")
        if value is None:
            return None
        try:
            return OptimizationPreference.model_validate(value)
        except (TypeError, ValueError):
            logger.warning("ignoring invalid inspiration optimization constraint")
            return None

    async def _offers_within_airfare_budget(
        self,
        offers: tuple[SafeFlightOffer, ...],
        budget: Money | None,
        *,
        trace_id: str | None,
    ) -> tuple[tuple[SafeFlightOffer, ...], dict[UUID, Decimal], int]:
        if budget is None:
            return offers, {}, 0

        comparable_amounts: dict[UUID, Decimal] = {}
        eligible: list[SafeFlightOffer] = []
        quote_cache: dict[tuple[str, str], Decimal | None] = {}
        conversion_failures = 0
        for offer in offers:
            amount: Decimal | None
            if offer.currency == budget.currency:
                amount = offer.total
            else:
                pair = (offer.currency, budget.currency)
                if pair not in quote_cache:
                    quote_cache[pair] = None
                    if self.exchange_rates is not None:
                        try:
                            quote = await self.exchange_rates.quote(
                                offer.currency,
                                budget.currency,
                                correlation_id=trace_id,
                            )
                            if (
                                quote.source_currency == offer.currency
                                and quote.target_currency == budget.currency
                                and quote.expires_at > self.clock.now()
                            ):
                                quote_cache[pair] = quote.rate
                        except Exception as exc:
                            logger.warning(
                                "airfare budget comparison failed",
                                extra={
                                    "source_currency": offer.currency,
                                    "target_currency": budget.currency,
                                    "trace_id": trace_id,
                                    "error_type": type(exc).__name__,
                                },
                            )
                rate = quote_cache[pair]
                amount = (
                    quantize_currency(offer.total * rate, budget.currency)
                    if rate is not None
                    else None
                )
            if amount is None:
                conversion_failures += 1
                continue
            comparable_amounts[offer.offer_id] = amount
            if amount <= budget.amount:
                eligible.append(offer)
        return tuple(eligible), comparable_amounts, conversion_failures

    async def _search(
        self,
        command: SearchFlightsCommand,
        state: OrchestrationState,
        *,
        airfare_budget: Money | None = None,
        optimization: OptimizationPreference | None = None,
    ) -> dict[str, Any]:
        criteria = FlightSearchCriteria(
            origin=command.origin,
            destination=command.destination,
            departure_date=command.departure_date,
            return_date=command.return_date,
            passengers=command.passengers,
            cabin=command.cabin,
            currency=command.currency,
            max_stops=command.max_stops,
            baggage_required=command.baggage_required,
            preferred_departure_start=command.preferred_departure_start,
            preferred_departure_end=command.preferred_departure_end,
        )
        user_id, _ = self._identity(state)
        result = await self.flight_search_application.search_exact(
            user_id, criteria, trace_id=state.get("trace_id")
        )
        now = self.clock.now()
        filtered_offers, comparable_amounts, conversion_failures = (
            await self._offers_within_airfare_budget(
                result.offers,
                airfare_budget,
                trace_id=state.get("trace_id"),
            )
        )
        highest_fare = bool(
            airfare_budget is not None
            and optimization is not None
            and optimization.metric == "fare"
            and optimization.direction == "maximize"
        )
        preference_timezone = None
        safe_context = state.get("safe_context")
        if isinstance(safe_context, dict):
            preferences = safe_context.get("travel_preferences_v1")
            if isinstance(preferences, dict):
                candidate_timezone = preferences.get("timezone")
                if isinstance(candidate_timezone, str) and candidate_timezone.strip():
                    preference_timezone = candidate_timezone
        if self.ranking_enabled:
            ranked_offers = self.ranking_service.rank(
                filtered_offers,
                now=now,
                requested_currency=criteria.currency,
                max_stops=criteria.max_stops,
                criteria=criteria,
                departure_timezone=resolve_departure_timezone(
                    criteria.origin, fallback_timezone=preference_timezone
                ),
            )
            if highest_fare:
                ranked_offers = tuple(
                    sorted(
                        ranked_offers,
                        key=lambda ranked: comparable_amounts[ranked.offer.offer_id],
                        reverse=True,
                    )
                )
            response_offers = [
                safe_offer_response(
                    ranked.offer,
                    rank=rank,
                    ranking_reasons=ranked.reasons,
                )
                for rank, ranked in enumerate(ranked_offers, start=1)
            ]
            ranking_version = self.ranking_service.ranking_version
        else:
            ordered_offers = provider_order_offers(
                filtered_offers,
                now=now,
                max_stops=criteria.max_stops,
            )
            if highest_fare:
                ordered_offers = tuple(
                    sorted(
                        ordered_offers,
                        key=lambda offer: comparable_amounts[offer.offer_id],
                        reverse=True,
                    )
                )
            response_offers = [
                safe_offer_response(offer, rank=index, ranking_reasons=())
                for index, offer in enumerate(ordered_offers, start=1)
            ]
            ranking_version = "provider-order-v0"

        response_status = result.status.value
        if result.status.value == "results" and airfare_budget is not None and not response_offers:
            response_status = "no_results"
        search_id = str(result.search_id) if result.search_id is not None else None
        presented_mapping = None
        if result.search_id is not None and response_offers:
            presented_mapping = {
                "search_id": search_id,
                "expires_at": min(offer.expires_at for offer in filtered_offers).isoformat(),
                "offers": [
                    {"rank": offer["rank"], "offer_id": offer["offer_id"]}
                    for offer in response_offers[:MAX_PRESENTED_OFFERS]
                ],
            }
        count = len(response_offers)
        if result.status.value == "provider_unavailable":
            message_vi = (
                "Nhà cung cấp chuyến bay đang tạm thời không khả dụng. Vui lòng thử lại sau."
            )
            message_en = "The flight provider is temporarily unavailable. Please try again later."
        elif count:
            if highest_fare:
                message_vi = (
                    f"Tìm thấy {count} lựa chọn trong ngân sách vé máy bay, "
                    "xếp từ giá cao nhất đến thấp nhất. Giá và chỗ ngồi có thể thay đổi; "
                    "hãy chọn rõ offer_id hoặc số lựa chọn."
                )
                message_en = (
                    f"Found {count} options within your airfare budget, ranked from highest "
                    "to lowest fare. Price and availability can change; explicitly choose "
                    "an offer_id or option number."
                )
            elif airfare_budget is not None:
                message_vi = (
                    f"Tìm thấy {count} lựa chọn trong ngân sách vé máy bay. "
                    "Giá và chỗ ngồi có thể thay đổi; hãy chọn rõ offer_id hoặc số lựa chọn."
                )
                message_en = (
                    f"Found {count} options within your airfare budget. Price and availability "
                    "can change; explicitly choose an offer_id or option number."
                )
            elif self.ranking_enabled:
                message_vi = (
                    f"Tìm thấy {count} lựa chọn đã xếp hạng theo giá, thời lượng, "
                    "số điểm dừng, hành lý và khung giờ nếu có. "
                    "Giá và chỗ ngồi có thể thay đổi; hãy chọn rõ offer_id hoặc số lựa chọn."
                )
                message_en = (
                    f"Found {count} deterministically ranked options using price, duration, "
                    "stops, baggage, and departure fit when provided. "
                    "Price and availability can change; explicitly choose an offer_id or option number."
                )
            else:
                message_vi = (
                    f"Tìm thấy {count} lựa chọn theo thứ tự nhà cung cấp. "
                    "Giá và chỗ ngồi có thể thay đổi; hãy chọn rõ offer_id hoặc số lựa chọn."
                )
                message_en = (
                    f"Found {count} options in provider order. Price and availability can change; "
                    "explicitly choose an offer_id or option number."
                )
        elif conversion_failures and airfare_budget is not None:
            message_vi = (
                "Đã tìm thấy giá vé nhưng chưa thể quy đổi an toàn để so sánh với ngân sách. "
                "Không hiển thị lựa chọn chưa xác minh; vui lòng thử lại sau."
            )
            message_en = (
                "I found fares, but could not safely convert their currencies to compare with "
                "your budget. No unverified option was shown; please try again later."
            )
        else:
            message_vi = (
                "Không tìm thấy lựa chọn còn hiệu lực cho yêu cầu này. Vui lòng tìm kiếm lại."
            )
            message_en = "No current options matched this request. Please search again."
        response: dict[str, Any] = {
            "action": "search_flights",
            "status": response_status,
            "persisted": True,
            "discovery_id": str(result.discovery_id),
            "ranking_version": ranking_version,
            "returned_results": len(response_offers),
            "selected_offer_id": None,
            "offers": response_offers,
            "provider_warnings": list(result.warnings),
            "retryable": result.retryable,
            "trace_id": result.trace_id,
            "optional_sources": {"weather": "not_configured", "reviews": "not_configured"},
            "message_vi": message_vi,
            "message_en": message_en,
            "_recommendation_destination": criteria.destination,
            "_recommendation_start_date": criteria.departure_date.isoformat(),
            "_recommendation_end_date": (
                criteria.return_date or criteria.departure_date
            ).isoformat(),
        }
        if airfare_budget is not None:
            response["airfare_budget"] = airfare_budget.model_dump(mode="json")
            response["budget_filter"] = {
                "applied": True,
                "conversion_failures": conversion_failures,
                "optimization": optimization.model_dump(mode="json")
                if optimization is not None
                else None,
            }
        if count and self.weather_service is not None:
            try:
                forecast = await self.weather_service.forecast_for_date(
                    criteria.destination,
                    criteria.departure_date,
                    correlation_id=state.get("trace_id"),
                    language="vi" if state.get("language") == "vi" else "en",
                )
                response["weather"] = safe_weather_summary(forecast)
                response["optional_sources"]["weather"] = forecast.status.value
            except Exception as exc:
                logger.warning(
                    "optional weather enrichment failed; returning flight results",
                    extra={
                        "destination": criteria.destination,
                        "trace_id": state.get("trace_id"),
                        "error_type": type(exc).__name__,
                    },
                )
                response["optional_sources"]["weather"] = "unavailable"
        if search_id is not None:
            response["search_id"] = search_id
        if presented_mapping is not None:
            response["_checkpoint_context"] = {"presented_offers_v1": presented_mapping}
        return response

    def _resolve_presented_offer_id(
        self,
        reference,
        state: OrchestrationState,
        session: Session,
        user_id: UUID,
    ) -> UUID | None:
        raw_context = state.get("safe_context", {})
        projection = (
            raw_context.get("presented_offers_v1") if isinstance(raw_context, dict) else None
        )
        if not isinstance(projection, dict):
            return None
        try:
            search_id = UUID(str(projection["search_id"]))
            projection_expiry = _db_utc(datetime.fromisoformat(str(projection["expires_at"])))
            requested_search_id = UUID(str(reference.search_id))
        except (KeyError, TypeError, ValueError):
            return None
        if requested_search_id != search_id:
            return None
        now = self.clock.now()
        if projection_expiry <= now:
            raise SafeActionError("offer_expired")
        mapping = next(
            (
                item
                for item in projection.get("offers", ())
                if isinstance(item, dict) and item.get("rank") == reference.rank
            ),
            None,
        )
        if mapping is None:
            return None
        try:
            mapped_offer_id = UUID(str(mapping["offer_id"]))
        except (KeyError, TypeError, ValueError):
            return None
        search = session.scalar(
            select(FlightSearchRecord).where(
                FlightSearchRecord.id == search_id,
                FlightSearchRecord.user_id == user_id,
            )
        )
        if search is None or _db_utc(search.expires_at) <= now:
            raise SafeActionError("offer_expired")
        offer = session.scalar(
            select(FlightOfferRecord).where(
                FlightOfferRecord.id == mapped_offer_id,
                FlightOfferRecord.search_id == search_id,
                FlightOfferRecord.user_id == user_id,
            )
        )
        if offer is None:
            return None
        if _db_utc(offer.expires_at) <= now:
            raise SafeActionError("offer_expired")
        return offer.id

    def _resolve_inspiration_offer_id(
        self,
        reference,
        state: OrchestrationState,
        session: Session,
        user_id: UUID,
    ) -> UUID | None:
        raw_context = state.get("safe_context", {})
        projection = (
            raw_context.get("trip_inspiration_v1") if isinstance(raw_context, dict) else None
        )
        if not isinstance(projection, dict):
            return None
        mapping = next(
            (
                item
                for item in projection.get("options", ())
                if isinstance(item, dict) and item.get("rank") == reference.rank
            ),
            None,
        )
        if mapping is None:
            return None
        try:
            offer_id = UUID(str(mapping["application_offer_id"]))
            search_id = UUID(str(mapping["search_id"]))
            option_expiry = _db_utc(datetime.fromisoformat(str(mapping["expires_at"])))
        except (KeyError, TypeError, ValueError):
            return None
        now = self.clock.now()
        if option_expiry <= now:
            raise SafeActionError("offer_expired")
        search = session.scalar(
            select(FlightSearchRecord).where(
                FlightSearchRecord.id == search_id,
                FlightSearchRecord.user_id == user_id,
            )
        )
        if search is None or _db_utc(search.expires_at) <= now:
            raise SafeActionError("offer_expired")
        offer = session.scalar(
            select(FlightOfferRecord).where(
                FlightOfferRecord.id == offer_id,
                FlightOfferRecord.search_id == search_id,
                FlightOfferRecord.user_id == user_id,
            )
        )
        if offer is None:
            return None
        if _db_utc(offer.expires_at) <= now:
            raise SafeActionError("offer_expired")
        return offer.id

    async def _start_booking(
        self,
        command: StartBookingCommand,
        state: OrchestrationState,
    ) -> dict[str, Any]:
        user_id, thread_id = self._identity(state)
        offer_id = command.offer_id
        if (
            command.presented_offer is None
            and command.inspiration_option is None
            and offer_id is None
            and state.get("selected_offer_id")
        ):
            try:
                offer_id = UUID(state["selected_offer_id"])
            except (TypeError, ValueError):
                offer_id = None
        if (
            offer_id is None
            and command.presented_offer is None
            and command.inspiration_option is None
        ):
            if state.get("safe_context", {}).get("reprice_required"):
                raise SafeActionError("offer_expired")
            return {
                "status": "clarification_required",
                "message_vi": "Vui lòng chọn rõ một offer_id hoặc số lựa chọn còn hiệu lực trước khi tạo bản nháp đặt chỗ.",
                "message_en": "Please explicitly choose an offer_id or a current option number before creating a booking draft.",
            }
        key_hash = hashlib.sha256(state["client_message_id"].encode()).hexdigest()[:24]
        idempotency_key = f"phase5:{thread_id}:{key_hash}"
        recommendation_destination: str | None = None
        recommendation_start_date: str | None = None
        recommendation_end_date: str | None = None
        session = self.session_factory()
        try:
            with session.begin():
                if command.inspiration_option is not None:
                    offer_id = self._resolve_inspiration_offer_id(
                        command.inspiration_option,
                        state,
                        session,
                        user_id,
                    )
                    if offer_id is None:
                        return {
                            "status": "clarification_required",
                            "message_vi": "Không tìm thấy lựa chọn cảm hứng còn hiệu lực. Vui lòng yêu cầu tìm lại các điểm đến.",
                            "message_en": "That inspiration option is not current. Please ask me to search destination ideas again.",
                        }
                if command.presented_offer is not None:
                    offer_id = self._resolve_presented_offer_id(
                        command.presented_offer,
                        state,
                        session,
                        user_id,
                    )
                    if offer_id is None:
                        return {
                            "status": "clarification_required",
                            "message_vi": "Không tìm thấy số lựa chọn trong kết quả tìm kiếm còn hiệu lực.",
                            "message_en": "That option number is not present in a current search owned by this account.",
                        }
                if offer_id is None:
                    return {
                        "status": "clarification_required",
                        "message_vi": "Vui lòng chọn một offer_id còn hiệu lực trước khi tạo bản nháp đặt chỗ.",
                        "message_en": "Please select a current offer_id before creating a booking draft.",
                    }
                offer = session.scalar(
                    select(FlightOfferRecord).where(
                        FlightOfferRecord.id == offer_id,
                        FlightOfferRecord.user_id == user_id,
                    )
                )
                if offer is None:
                    return {
                        "status": "clarification_required",
                        "message_vi": "Không tìm thấy ưu đãi thuộc tài khoản này.",
                        "message_en": "No offer with that ID belongs to this account.",
                    }
                if _db_utc(offer.expires_at) <= self.clock.now():
                    raise SafeActionError("offer_expired")
                typed_offer = FlightOffer.model_validate(offer.offer_snapshot)
                recommendation_destination = typed_offer.segments[-1].destination
                recommendation_start_date = typed_offer.segments[0].departure_at.date().isoformat()
                recommendation_end_date = typed_offer.segments[-1].departure_at.date().isoformat()
                if command.traveler_profile_ids:
                    owned = set(
                        session.scalars(
                            select(TravelerProfileRecord.id).where(
                                TravelerProfileRecord.user_id == user_id,
                                TravelerProfileRecord.id.in_(command.traveler_profile_ids),
                            )
                        ).all()
                    )
                    if owned != set(command.traveler_profile_ids):
                        return {
                            "status": "clarification_required",
                            "message_vi": "Một hồ sơ hành khách không thuộc tài khoản này.",
                            "message_en": "One traveler profile does not belong to this account.",
                        }
                record = session.scalar(
                    select(BookingIntentRecord).where(
                        BookingIntentRecord.idempotency_key == idempotency_key,
                        BookingIntentRecord.user_id == user_id,
                    )
                )
                if record is None:
                    record = BookingIntentRecord(
                        user_id=user_id,
                        source_offer_id=offer_id,
                        thread_id=thread_id,
                        status="draft",
                        traveler_profile_ids=[str(value) for value in command.traveler_profile_ids],
                        idempotency_key=idempotency_key,
                    )
                    session.add(record)
                    session.flush()
                intent_id = record.id
        finally:
            session.close()
        return {
            "action": "start_booking",
            "status": "draft",
            "persisted": True,
            "booking_intent_id": str(intent_id),
            "selected_offer_id": str(offer_id),
            "_recommendation_destination": recommendation_destination,
            "_recommendation_start_date": recommendation_start_date,
            "_recommendation_end_date": recommendation_end_date,
            "message_vi": f"Đã tạo bản nháp đặt chỗ {intent_id}. Chưa đặt vé, giữ chỗ hoặc thanh toán; bước xác nhận sẽ do dịch vụ giao dịch riêng xử lý.",
            "message_en": f"Created booking draft {intent_id}. Nothing was booked, held, or charged; confirmation is handled by a separate transactional service.",
        }

    def _confirm_booking(
        self,
        command: ConfirmBookingCommand,
        state: OrchestrationState,
    ) -> dict[str, Any]:
        user_id, _ = self._identity(state)
        intent_id = command.booking_intent_id or (
            UUID(state["booking_intent_id"]) if state.get("booking_intent_id") else None
        )
        if intent_id is not None:
            session = self.session_factory()
            try:
                with session.begin():
                    owned = session.scalar(
                        select(BookingIntentRecord.id).where(
                            BookingIntentRecord.id == intent_id,
                            BookingIntentRecord.user_id == user_id,
                        )
                    )
            finally:
                session.close()
            if owned is None:
                intent_id = None
        if intent_id is None:
            return {
                "status": "clarification_required",
                "message_vi": "Không tìm thấy bản nháp đặt chỗ thuộc tài khoản này.",
                "message_en": "No booking draft with that ID belongs to this account.",
            }
        return {
            "action": "confirm_booking",
            "status": "confirmation_required",
            "booking_intent_id": str(intent_id),
            "message_vi": "Chat không thể tự xác nhận mua hoặc thanh toán. Hãy dùng bước xác nhận giao dịch có kiểm tra lại giá ở Phase 6.",
            "message_en": "Chat cannot authorize purchase or payment. Use the Phase 6 transactional confirmation step, which reprices the offer first.",
        }

    def _manage_booking(
        self,
        command: ManageBookingCommand,
        state: OrchestrationState,
    ) -> dict[str, Any]:
        user_id, _ = self._identity(state)
        if command.booking_id is None:
            return {
                "status": "clarification_required",
                "message_vi": "Vui lòng cung cấp booking_id thuộc tài khoản này.",
                "message_en": "Please provide a booking_id from this account.",
            }
        session = self.session_factory()
        try:
            with session.begin():
                booking = session.scalar(
                    select(BookingRecord).where(
                        BookingRecord.id == command.booking_id,
                        BookingRecord.user_id == user_id,
                    )
                )
                status_value = booking.status if booking else None
        finally:
            session.close()
        if status_value is None:
            return {
                "status": "clarification_required",
                "message_vi": "Không tìm thấy đặt chỗ thuộc tài khoản này.",
                "message_en": "No booking with that ID belongs to this account.",
            }
        return {
            "action": "manage_booking",
            "status": status_value,
            "booking_id": str(command.booking_id),
            "message_vi": "Đã tải trạng thái. Hủy hoặc hoàn tiền cần khả năng của nhà cung cấp và một bước xác nhận riêng; chat chưa thực hiện hành động đó.",
            "message_en": "Status loaded. Cancellation or refund requires provider capability and separate confirmation; chat did not execute it.",
        }

    def _create_watch(
        self,
        command: CreateWatchCommand,
        state: OrchestrationState,
    ) -> dict[str, Any]:
        user_id, thread_id = self._identity(state)
        watch_id = uuid5(
            NAMESPACE_URL,
            f"phase5-watch:{user_id}:{thread_id}:{state['client_message_id']}",
        )
        criteria = {
            "origin": command.origin,
            "destination": command.destination,
            "departure_date_from": command.departure_date_from.isoformat(),
            "departure_date_to": command.departure_date_to.isoformat(),
            "action_mode": "notify",
        }
        if command.maximum_total is not None:
            criteria["maximum_total"] = {
                "amount": command.maximum_total,
                "currency": command.currency,
            }
        session = self.session_factory()
        try:
            with session.begin():
                record = session.scalar(
                    select(FlightWatchRecord).where(
                        FlightWatchRecord.id == watch_id,
                        FlightWatchRecord.user_id == user_id,
                    )
                )
                if record is None:
                    record = FlightWatchRecord(
                        id=watch_id,
                        user_id=user_id,
                        criteria=criteria,
                        status="draft",
                        next_run_at=None,
                    )
                    session.add(record)
                    session.flush()
        finally:
            session.close()
        auto_note_vi = (
            " Yêu cầu tự mua chỉ được ghi nhận trong bản nháp; chưa có ủy quyền mua."
            if command.auto_buy_requested
            else ""
        )
        auto_note_en = (
            " The auto-buy request is draft-only; no purchase mandate exists."
            if command.auto_buy_requested
            else ""
        )
        return {
            "action": "create_watch",
            "status": "draft",
            "persisted": True,
            "watch_draft_id": str(watch_id),
            "message_vi": f"Đã tạo bản nháp theo dõi {watch_id}; chưa kích hoạt thông báo.{auto_note_vi}",
            "message_en": f"Created watch draft {watch_id}; notifications are not active yet.{auto_note_en}",
        }

    def _manage_watch(
        self,
        command: ManageWatchCommand,
        state: OrchestrationState,
    ) -> dict[str, Any]:
        user_id, _ = self._identity(state)
        watch_id = command.watch_id or (
            UUID(state["watch_draft_id"]) if state.get("watch_draft_id") else None
        )
        if watch_id is None:
            return {
                "status": "clarification_required",
                "message_vi": "Vui lòng cung cấp watch_id thuộc tài khoản này.",
                "message_en": "Please provide a watch_id from this account.",
            }
        session = self.session_factory()
        try:
            with session.begin():
                record = session.scalar(
                    select(FlightWatchRecord).where(
                        FlightWatchRecord.id == watch_id,
                        FlightWatchRecord.user_id == user_id,
                    )
                )
                status_value = record.status if record else None
        finally:
            session.close()
        if status_value is None:
            return {
                "status": "clarification_required",
                "message_vi": "Không tìm thấy theo dõi thuộc tài khoản này.",
                "message_en": "No watch with that ID belongs to this account.",
            }
        return {
            "action": "manage_watch",
            "status": status_value,
            "watch_draft_id": str(watch_id),
            "message_vi": "Đã tải trạng thái theo dõi. Thay đổi trạng thái sẽ được xử lý bởi dịch vụ Phase 7, không do mô hình tự thực hiện.",
            "message_en": "Watch status loaded. State changes are handled by the Phase 7 service, not executed by the model.",
        }

    def _profile(
        self,
        command: UpdateProfileCommand,
        state: OrchestrationState,
    ) -> dict[str, Any]:
        user_id, _ = self._identity(state)
        if command.traveler_profile_id is not None:
            session = self.session_factory()
            try:
                with session.begin():
                    owned = session.scalar(
                        select(TravelerProfileRecord.id).where(
                            TravelerProfileRecord.id == command.traveler_profile_id,
                            TravelerProfileRecord.user_id == user_id,
                        )
                    )
            finally:
                session.close()
            if owned is None:
                return {
                    "status": "clarification_required",
                    "message_vi": "Không tìm thấy hồ sơ hành khách thuộc tài khoản này.",
                    "message_en": "No traveler profile with that ID belongs to this account.",
                }
        return {
            "action": "update_profile",
            "status": "consent_required",
            "message_vi": "Chat chưa thay đổi hồ sơ. Hãy kiểm tra các trường và lưu qua API hồ sơ có kiểm soát phiên bản; dữ liệu nhạy cảm không được gửi tới mô hình.",
            "message_en": "Chat did not change the profile. Review fields and save through the versioned profile API; sensitive values are not sent to the model.",
        }


@dataclass(frozen=True)
class OrchestrationTurnResult:
    created: bool
    user_message: MessageView
    assistant_message: MessageView
    checkpoint_version: int
    safe_result: dict[str, Any]
    errors: tuple[str, ...]


@dataclass
class _TurnLockEntry:
    lock: asyncio.Lock
    references: int = 0


class OrchestrationService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        llm: LLMProvider,
        flight_search: FlightSearchService,
        *,
        flight_search_application: FlightSearchApplicationService | None = None,
        destination_recommendations: DestinationRecommendationService | None = None,
        trip_inspiration_service: TripInspirationService | None = None,
        provider_registry: ProviderRegistry | None = None,
        feature_settings: FeatureSettings | None = None,
        trip_discovery_settings: TripDiscoverySettings | None = None,
        trip_discovery_service: TripDiscoveryService | None = None,
        location_resolution_service: LocationResolutionService | None = None,
        provider_executor: ProviderExecutor | None = None,
        clock: Clock | None = None,
        ranking_service: FlightRankingService | None = None,
        exchange_rates: ExchangeRateProvider | None = None,
        weather_service: WeatherService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.llm = llm
        self.provider_registry = provider_registry
        self.location_resolution_service = location_resolution_service
        self.flight_search = flight_search
        self.flight_search_application = (
            flight_search_application
            or FlightSearchApplicationService(session_factory, flight_search, clock=clock)
        )
        self.clock = clock or SystemClock()
        self.exchange_rates = exchange_rates
        self.weather_service = weather_service
        self.feature_settings = feature_settings or FeatureSettings()
        self.ranking_service = ranking_service or FlightRankingService()
        self._turn_locks: dict[tuple[UUID, UUID, str], _TurnLockEntry] = {}
        self._turn_locks_guard = asyncio.Lock()
        self.trip_discovery_settings = trip_discovery_settings or TripDiscoverySettings()
        self.trip_discovery_service = trip_discovery_service or TripDiscoveryService(
            clock=self.clock,
            settings=self.trip_discovery_settings,
        )
        if (
            self.location_resolution_service is None
            and self.feature_settings.dynamic_location_resolution_enabled
            and provider_registry is not None
            and provider_executor is not None
        ):
            self.location_resolution_service = LocationResolutionService(
                provider_registry.locations,
                provider_executor,
                catalog_provider=CatalogLocationProvider(
                    environment=provider_executor.environment,
                ),
                clock=self.clock,
            )
        if (
            self.weather_service is None
            and provider_registry is not None
            and provider_executor is not None
        ):
            self.weather_service = WeatherService(
                provider_registry.weather,
                WeatherCache(),
                provider_executor,
                self.clock,
            )
        registry_places = (
            provider_registry.places
            if provider_registry is not None and provider_registry.places is not None
            else None
        )
        self.destination_recommendations = (
            destination_recommendations
            or DestinationRecommendationService(
                provider=registry_places,
                llm=llm,
                clock=self.clock,
                enabled=self.feature_settings.destination_recommendations_enabled,
                llm_enabled=self.feature_settings.places_llm_ranking_enabled,
                llm_generation_enabled=self.feature_settings.places_llm_generation_enabled,
            )
        )
        self.trip_inspiration_service = trip_inspiration_service
        if self.trip_inspiration_service is None and self.feature_settings.trip_inspiration_enabled:
            self.trip_inspiration_service = TripInspirationService(
                llm=llm,
                location_resolution=self.location_resolution_service,
                flight_search_application=self.flight_search_application,
                clock=self.clock,
                settings=TripInspirationSettings.from_environment(),
                exchange_rates=self.exchange_rates,
            )
        self.action_executor = OrchestrationActionExecutor(
            session_factory,
            flight_search,
            destination_recommendations=self.destination_recommendations,
            flight_search_application=self.flight_search_application,
            weather_service=self.weather_service,
            clock=self.clock,
            exchange_rates=self.exchange_rates,
            ranking_service=self.ranking_service,
            ranking_enabled=self.feature_settings.flight_ranking_enabled,
        )
        self.graph = build_orchestration_graph(
            llm,
            self.action_executor,
            feature_settings=self.feature_settings,
            trip_discovery_service=self.trip_discovery_service,
            flight_search_application=self.flight_search_application,
            destination_recommendations=self.destination_recommendations,
            trip_inspiration_service=self.trip_inspiration_service,
            location_resolution_service=self.location_resolution_service,
            clock=self.clock,
            ranking_service=self.ranking_service,
        )

    @classmethod
    def from_environment(
        cls,
        session_factory: Callable[[], Session],
        *,
        feature_settings: FeatureSettings | None = None,
    ) -> OrchestrationService:
        active_features = feature_settings or FeatureSettings.from_environment()
        trip_discovery_settings = TripDiscoverySettings.from_environment()
        provider_settings = ProviderSettings.from_environment()
        clock = SystemClock()
        exchange_rates = build_exchange_rate_provider(
            clock=clock,
            environment=provider_settings.execution_mode,
        )
        registry = build_provider_registry(provider_settings, clock=clock)
        provider_executor = ProviderExecutor.from_settings(provider_settings, clock=clock)
        flight_search = FlightSearchService(
            registry.flight,
            SearchCache(max_entries=provider_settings.search_cache_max_entries),
            provider_executor,
            clock,
        )
        llm_settings = LLMSettings.from_environment()
        llm = build_llm_provider(
            llm_settings,
            trip_discovery_enabled=active_features.trip_discovery_enabled,
        )
        destination_recommendations = DestinationRecommendationService(
            provider=registry.places,
            executor=provider_executor,
            llm=llm,
            clock=clock,
            enabled=active_features.destination_recommendations_enabled,
            llm_enabled=active_features.places_llm_ranking_enabled,
            llm_generation_enabled=active_features.places_llm_generation_enabled,
            maximum_places=provider_settings.places_recommendation_limit,
            maximum_candidates=provider_settings.places_recommendation_max_candidates,
            timeout_seconds=provider_settings.places_recommendation_timeout_seconds,
            llm_generation_timeout_seconds=llm_settings.timeout_seconds,
            cache_ttl_seconds=provider_settings.places_cache_ttl_seconds,
            curated_fallback_enabled=provider_settings.places_curated_fallback_enabled,
        )
        flight_search_application = FlightSearchApplicationService(
            session_factory,
            flight_search,
            clock=clock,
        )
        location_resolution_service = LocationResolutionService(
            registry.locations,
            provider_executor,
            catalog_provider=CatalogLocationProvider(environment=provider_settings.execution_mode),
            cache=LocationResolutionCache(
                max_entries=provider_settings.location_cache_max_entries,
            ),
            result_limit=provider_settings.location_lookup_limit,
            cache_ttl_seconds=provider_settings.location_cache_ttl_seconds,
            clock=clock,
        )
        trip_inspiration_service = (
            TripInspirationService(
                llm=llm,
                location_resolution=location_resolution_service,
                flight_search_application=flight_search_application,
                clock=clock,
                settings=TripInspirationSettings.from_environment(),
                exchange_rates=exchange_rates,
            )
            if active_features.trip_inspiration_enabled
            else None
        )
        return cls(
            session_factory,
            llm,
            flight_search,
            flight_search_application=flight_search_application,
            destination_recommendations=destination_recommendations,
            trip_inspiration_service=trip_inspiration_service,
            provider_registry=registry,
            location_resolution_service=location_resolution_service,
            provider_executor=provider_executor,
            feature_settings=active_features,
            trip_discovery_settings=trip_discovery_settings,
            trip_discovery_service=TripDiscoveryService(
                clock=clock,
                settings=trip_discovery_settings,
            ),
            clock=clock,
            exchange_rates=exchange_rates,
            weather_service=WeatherService(
                registry.weather,
                WeatherCache(),
                provider_executor,
                clock,
            ),
        )

    async def aclose(self) -> None:
        close_llm = getattr(self.llm, "aclose", None)
        if close_llm is not None:
            await close_llm()
        if self.provider_registry is not None:
            await self.provider_registry.aclose()

    async def process_turn(
        self,
        principal: AuthenticatedPrincipal,
        thread_id: UUID,
        *,
        content: str,
        client_message_id: str,
        trace_id: str,
    ) -> OrchestrationTurnResult:
        key = (principal.user_id, thread_id, client_message_id)
        async with self._turn_locks_guard:
            entry = self._turn_locks.get(key)
            if entry is None:
                entry = _TurnLockEntry(lock=asyncio.Lock())
                self._turn_locks[key] = entry
            entry.references += 1
        try:
            async with entry.lock:
                return await self._process_turn_locked(
                    principal,
                    thread_id,
                    content=content,
                    client_message_id=client_message_id,
                    trace_id=trace_id,
                )
        finally:
            async with self._turn_locks_guard:
                entry.references -= 1
                if entry.references == 0 and self._turn_locks.get(key) is entry:
                    del self._turn_locks[key]

    async def _wait_for_replay(
        self,
        principal: AuthenticatedPrincipal,
        thread_id: UUID,
        *,
        client_message_id: str,
        user_message: MessageView,
    ) -> OrchestrationTurnResult:
        assistant_key = _assistant_key(client_message_id)
        for _ in range(_REPLAY_POLL_ATTEMPTS):
            session = self.session_factory()
            try:
                with session.begin():
                    assistant_record = MessageRepository(session, principal).get_by_client_id(
                        thread_id, assistant_key
                    )
                    if assistant_record is not None and not _is_processing_replay(assistant_record):
                        result, errors = _stored_replay(assistant_record)
                        checkpoint_version = _checkpoint_version_for_assistant(
                            session,
                            principal,
                            thread_id,
                            assistant_record.id,
                        )
                        logger.info(
                            "chat_replay_metric",
                            extra={"metric_name": "chat_replay_total", "outcome": "waited"},
                        )
                        return OrchestrationTurnResult(
                            created=False,
                            user_message=user_message,
                            assistant_message=_message_view(assistant_record),
                            checkpoint_version=checkpoint_version,
                            safe_result=result,
                            errors=errors,
                        )
            finally:
                session.close()
            await asyncio.sleep(_REPLAY_POLL_INTERVAL_SECONDS)
        raise ConcurrencyConflictError("turn is still processing; retry the same client_message_id")

    async def _process_turn_locked(
        self,
        principal: AuthenticatedPrincipal,
        thread_id: UUID,
        *,
        content: str,
        client_message_id: str,
        trace_id: str,
    ) -> OrchestrationTurnResult:
        session = self.session_factory()
        existing_record: ChatMessageRecord | None = None
        context = None
        checkpoint_version = 0
        previous_state = CheckpointState()
        preference_projection: dict[str, Any] | None = None
        try:
            with session.begin():
                messages = MessageService(session, clock=self.clock)
                user_result = messages.append_user(
                    principal,
                    thread_id,
                    content=content,
                    client_message_id=client_message_id,
                )
                message_repository = MessageRepository(session, principal)
                existing_record = message_repository.get_by_client_id(
                    thread_id, _assistant_key(client_message_id)
                )
                if existing_record is None:
                    context = messages.build_context(
                        principal, thread_id, include_current_extra=True
                    )
                    checkpoint = context.checkpoint
                    checkpoint_version = checkpoint.version if checkpoint else 0
                    previous_state = checkpoint.state if checkpoint else CheckpointState()
                    if self.feature_settings.travel_preferences_enabled:
                        preference_projection = TravelPreferenceService(
                            session,
                            clock=self.clock,
                        ).planning_projection(principal)
                    message_repository.add(
                        ChatMessageRecord(
                            user_id=principal.user_id,
                            thread_id=thread_id,
                            role=ChatRole.ASSISTANT.value,
                            content="Processing request.",
                            sequence=message_repository.next_sequence(thread_id),
                            client_message_id=_assistant_key(client_message_id),
                            safe_result=dict(_PROCESSING_SAFE_RESULT),
                            safe_result_schema_version=SAFE_RESULT_SCHEMA_VERSION,
                            safe_errors=[],
                        )
                    )
        finally:
            session.close()

        if existing_record is not None:
            if _is_processing_replay(existing_record):
                return await self._wait_for_replay(
                    principal,
                    thread_id,
                    client_message_id=client_message_id,
                    user_message=user_result.message,
                )
            replay_result, replay_errors = _stored_replay(existing_record)
            checkpoint_session = self.session_factory()
            try:
                with checkpoint_session.begin():
                    replay_checkpoint_version = _checkpoint_version_for_assistant(
                        checkpoint_session,
                        principal,
                        thread_id,
                        existing_record.id,
                    )
            finally:
                checkpoint_session.close()
            logger.info(
                "chat_replay_metric",
                extra={"metric_name": "chat_replay_total", "outcome": "replayed"},
            )
            return OrchestrationTurnResult(
                created=False,
                user_message=user_result.message,
                assistant_message=_message_view(existing_record),
                checkpoint_version=replay_checkpoint_version,
                safe_result=replay_result,
                errors=replay_errors,
            )

        if context is None:
            raise ConcurrencyConflictError("turn context could not be established")

        safe_context = dict(previous_state.safe_context)
        safe_context.pop("traveler_profiles", None)
        if self.feature_settings.travel_preferences_enabled and preference_projection is not None:
            safe_context["travel_preferences_v1"] = preference_projection
        else:
            safe_context.pop("travel_preferences_v1", None)

        graph_input: OrchestrationState = {
            "authenticated_user_id": str(principal.user_id),
            "thread_id": str(thread_id),
            "client_message_id": client_message_id,
            "trace_id": trace_id,
            "checkpoint_version": checkpoint_version,
            "current_message": user_result.message.content,
            "locale": context.thread.locale.value,
            "recent_messages": [
                {"role": message.role.value, "content": message.content}
                for message in context.messages
                if message.id != user_result.message.id
            ],
            "safe_summary": context.summary,
            "selected_offer_id": str(previous_state.selected_offer_id)
            if previous_state.selected_offer_id
            else None,
            "booking_intent_id": str(previous_state.booking_intent_id)
            if previous_state.booking_intent_id
            else None,
            "watch_draft_id": str(previous_state.watch_draft_id)
            if previous_state.watch_draft_id
            else None,
            "safe_context": safe_context,
            "errors": [],
        }
        try:
            final = await self.graph.ainvoke(graph_input)
        except Exception:
            final = {
                **graph_input,
                "final_response": (
                    "Không thể xử lý lượt chat này. Trạng thái chưa bị thay đổi. "
                    if context.thread.locale.value == "vi"
                    else "This turn could not be processed. State was not changed. "
                ),
                "safe_result": {"status": "recoverable_error"},
                "errors": ["orchestration_error"],
                "checkpoint_changed": False,
            }

        errors_list = list(dict.fromkeys(final.get("errors", [])))
        try:
            safe_result = sanitize_safe_result(dict(final.get("safe_result", {})))
        except SafeResultError:
            safe_result = {"status": "recoverable_error"}
            errors_list.append("safe_result_invalid")
        errors = tuple(dict.fromkeys(errors_list))
        checkpoint_state = None
        if final.get("checkpoint_changed") and not errors:
            safe_context = _checkpoint_context(
                previous_state.safe_context,
                final.get("safe_context", {}),
                safe_result,
                selected_offer_id=final.get("selected_offer_id"),
                booking_intent_id=final.get("booking_intent_id"),
                watch_draft_id=final.get("watch_draft_id"),
            )
            try:
                checkpoint_state = CheckpointState(
                    current_intent=(final.get("command") or {}).get("intent"),
                    plan=tuple(final.get("plan", ())),
                    selected_offer_id=final.get("selected_offer_id"),
                    booking_intent_id=final.get("booking_intent_id"),
                    watch_draft_id=final.get("watch_draft_id"),
                    safe_context=safe_context,
                )
            except ValueError:
                errors = tuple(dict.fromkeys([*errors, "checkpoint_state_invalid"]))
                checkpoint_state = None

        try:
            persisted_errors = validate_safe_errors(errors)
        except SafeResultError:
            persisted_errors = ["safe_result_invalid"]
            errors = ("safe_result_invalid",)
        completion_session = self.session_factory()
        try:
            with completion_session.begin():
                thread = ThreadRepository(completion_session, principal).lock(thread_id)
                message_repository = MessageRepository(completion_session, principal)
                assistant_record = message_repository.get_by_client_id(
                    thread_id, _assistant_key(client_message_id)
                )
                saved_checkpoint = None
                if assistant_record is None:
                    sanitized = sanitize_message_text(final["final_response"])
                    assistant_record = message_repository.add(
                        ChatMessageRecord(
                            user_id=principal.user_id,
                            thread_id=thread_id,
                            role=ChatRole.ASSISTANT.value,
                            content=sanitized.text,
                            sequence=message_repository.next_sequence(thread_id),
                            client_message_id=_assistant_key(client_message_id),
                            safe_result=safe_result,
                            safe_result_schema_version=SAFE_RESULT_SCHEMA_VERSION,
                            safe_errors=persisted_errors,
                        )
                    )
                    can_complete = True
                else:
                    can_complete = _is_processing_replay(assistant_record)
                    if can_complete:
                        sanitized = sanitize_message_text(final["final_response"])
                        assistant_record.content = sanitized.text
                        assistant_record.safe_result = safe_result
                        assistant_record.safe_result_schema_version = SAFE_RESULT_SCHEMA_VERSION
                        assistant_record.safe_errors = persisted_errors

                if can_complete:
                    thread.updated_at = self.clock.now()
                    if checkpoint_state is not None:
                        saved_checkpoint = CheckpointService(
                            completion_session, clock=self.clock
                        ).save(
                            principal,
                            thread_id,
                            state=checkpoint_state,
                            expected_version=checkpoint_version,
                            last_message_id=assistant_record.id,
                        )
                assistant = _message_view(assistant_record)
                final_checkpoint_version = (
                    saved_checkpoint.version
                    if saved_checkpoint
                    else (
                        _checkpoint_version_for_assistant(
                            completion_session,
                            principal,
                            thread_id,
                            assistant_record.id,
                        )
                        if not can_complete
                        else checkpoint_version
                    )
                )
        finally:
            completion_session.close()

        return OrchestrationTurnResult(
            created=user_result.created,
            user_message=user_result.message,
            assistant_message=assistant,
            checkpoint_version=final_checkpoint_version,
            safe_result=safe_result,
            errors=errors,
        )
