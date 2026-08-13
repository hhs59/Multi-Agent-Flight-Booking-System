from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import date, timedelta
from typing import Annotated, Any, Protocol, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from agent_system.domain.limits import MAX_PRESENTED_OFFERS
from agent_system.domain.location_resolution import (
    LocationLookupRequest,
    LocationSuggestion,
    LocationSuggestionKind,
    normalize_location_query,
)
from agent_system.domain.orchestration import (
    COMMAND_ADAPTER,
    AdviceRequest,
    AgentCommand,
    AgentIntent,
    InterpretedLocation,
    PlanningLocationCandidate,
    PlanningMessage,
    PlanningPendingClarification,
    PlanningRequest,
    PresentedOfferReference,
    SearchFlightsCommand,
    SearchInspirationOptionCommand,
    StartBookingCommand,
    UnclearCommand,
)
from agent_system.domain.travel_preferences import TravelPreferencesPlanningProjection
from agent_system.domain.trip_discovery import (
    ClarificationReason,
    ClarificationRequired,
    DynamicDestinationChoice,
    DynamicOriginChoice,
    DynamicOriginChoices,
    ExecutableFlightSearch,
    LocationReference,
    PendingDestinationConfirmation,
    TravelDateWindow,
    TripDiscoveryCommand,
    TripDiscoverySearchResult,
)
from agent_system.domain.trip_inspiration import TripInspirationCommand
from agent_system.llm_providers import (
    LLMOutputError,
    LLMProvider,
    LLMUnavailableError,
    RuleBasedLLMProvider,
    normalize_pending_field_plan,
)
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.errors import ProviderError
from agent_system.providers.localization import AirportCatalog, normalize_vietnamese_alias
from agent_system.security.sanitization import sanitize_for_llm, sanitize_text
from agent_system.services.destination_recommendations import (
    DestinationRecommendationMetric,
    DestinationRecommendationService,
)
from agent_system.services.feature_settings import FeatureSettings
from agent_system.services.flight_ranking import (
    FlightRankingService,
    provider_order_offers,
    resolve_departure_timezone,
    safe_offer_response,
)
from agent_system.services.flight_search_application import (
    DiscoveryBudgetExceeded,
    FlightSearchApplicationService,
)
from agent_system.services.location_resolution import LocationResolutionService
from agent_system.services.semantic_updates import (
    SemanticPolicyRejectionError,
    ValidatedSemanticUpdates,
    apply_semantic_updates,
)
from agent_system.services.trip_discovery import TripDiscoveryService
from agent_system.services.trip_inspiration import TripInspirationService

logger = logging.getLogger(__name__)


def _semantic_observability(semantic_updates: Any) -> dict[str, object]:
    kinds: list[str] = []
    operations: list[str] = []
    for field in (
        "temporal",
        "budget",
        "passengers",
        "origin",
        "destination",
        "search",
        "result_reference",
    ):
        semantic = getattr(semantic_updates, field, None)
        if semantic is None:
            continue
        operation = getattr(semantic, "operation", None)
        if operation not in {None, "none"}:
            kinds.append(field)
            operations.append(f"{field}:{operation}")
    return {
        "semantic_update_kind": tuple(kinds),
        "semantic_update_operation": tuple(operations),
    }


def unique_errors(left: list[str], right: list[str]) -> list[str]:
    return list(dict.fromkeys([*left, *right]))


class OrchestrationState(TypedDict, total=False):
    authenticated_user_id: str
    thread_id: str
    client_message_id: str
    trace_id: str
    checkpoint_version: int
    current_message: str
    locale: str
    recent_messages: list[dict[str, str]]
    safe_summary: str | None
    selected_offer_id: str | None
    booking_intent_id: str | None
    watch_draft_id: str | None
    command: dict[str, Any] | None
    language: str
    plan: list[str]
    safe_context: dict[str, Any]
    dialogue_act: str
    conversation_action: str
    destination_scope: str | None
    clarification_action: str | None
    interpreted_destination: dict[str, Any] | None
    semantic_updates: dict[str, Any]
    semantic_overrides: dict[str, Any]
    safe_result: dict[str, Any]
    destination_recommendation_input: dict[str, Any]
    trip_inspiration_checkpoint: dict[str, Any]
    discovery_result: dict[str, Any]
    discovery_route: str
    trip_discovery_request: bool
    final_response: str
    checkpoint_changed: bool
    errors: Annotated[list[str], unique_errors]


class ActionExecutor(Protocol):
    async def execute(
        self,
        command: AgentCommand,
        state: OrchestrationState,
    ) -> dict[str, Any]: ...


class NoopActionExecutor:
    async def execute(
        self,
        command: AgentCommand,
        state: OrchestrationState,
    ) -> dict[str, Any]:
        del state
        return {"action": command.intent.value, "status": "clarification_required"}


def _command(state: OrchestrationState) -> AgentCommand:
    return COMMAND_ADAPTER.validate_python(state.get("command"))


def _route(
    state: OrchestrationState,
    *,
    trip_discovery_enabled: bool = True,
    trip_inspiration_enabled: bool = False,
) -> str:
    intent = _command(state).intent
    if intent is AgentIntent.TRIP_DISCOVERY:
        return "trip_discovery" if trip_discovery_enabled else "clarifier"
    if intent is AgentIntent.TRIP_INSPIRATION:
        return "trip_inspiration" if trip_inspiration_enabled else "clarifier"
    if intent is AgentIntent.SEARCH_INSPIRATION_OPTION:
        return "inspiration_option_search" if trip_inspiration_enabled else "clarifier"
    return {
        AgentIntent.SEARCH_FLIGHTS: "flight_search",
        AgentIntent.ADVISE: "advisor",
        AgentIntent.START_BOOKING: "booking_coordinator",
        AgentIntent.CONFIRM_BOOKING: "booking_coordinator",
        AgentIntent.MANAGE_BOOKING: "booking_coordinator",
        AgentIntent.CREATE_WATCH: "watch_coordinator",
        AgentIntent.MANAGE_WATCH: "watch_coordinator",
        AgentIntent.UPDATE_PROFILE: "profile_coordinator",
        AgentIntent.UNCLEAR: "clarifier",
    }[intent]


_VIETNAMESE_DIACRITICS = re.compile(
    r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", re.IGNORECASE
)
_ENGLISH_TURN_MARKERS = re.compile(
    r"\b(?:i|we|me|my|our|yes|no|sure|thanks|want|need|would|could|please|from|to|next|week|flight|fare|book|find|show|go|travel|where|when|how|what|why|which|cheapest|highest|direct|option|explain)\b",
    re.IGNORECASE,
)
_VIETNAMESE_TURN_MARKERS = re.compile(
    r"\b(?:tôi|mình|muốn|cần|hãy|vui lòng|tuần|chuyến|bay|vé|đi|đến|từ|ngày|tháng|"
    r"đâu|nào|giá|triệu|đúng|rồi|được|không|vâng|có|thôi|sao|nữa|"
    r"toi|minh|muon|can|tuan|chuyen|ve|den|tu|ngay|thang|dau|nao|gia|trieu|"
    r"dung|roi|duoc|khong|vang|co|thoi|sao|nua)\b",
    re.IGNORECASE,
)


def _turn_language(message: str, model_language: str, locale: str = "en") -> str:
    """Prefer current-message grammar without treating an accented place name as Vietnamese."""
    vietnamese_hits = len(_VIETNAMESE_TURN_MARKERS.findall(message))
    english_hits = len(_ENGLISH_TURN_MARKERS.findall(message))
    if vietnamese_hits > english_hits:
        return "vi"
    if english_hits > vietnamese_hits:
        return "en"
    if model_language in {"vi", "en"}:
        return model_language
    if _VIETNAMESE_DIACRITICS.search(message):
        return "vi"
    return "vi" if locale == "vi" else "en"


def _localized_fallback(language: str, error: str) -> str:
    if language == "vi":
        messages = {
            "llm_unavailable": "Hệ thống ngôn ngữ đang tạm thời không khả dụng. Vui lòng thử lại; trạng thái cuộc trò chuyện chưa bị thay đổi.",
            "llm_timeout": "Hệ thống ngôn ngữ phản hồi quá lâu. Vui lòng thử lại; trạng thái cuộc trò chuyện chưa bị thay đổi.",
            "llm_http_401_403": "Dịch vụ ngôn ngữ chưa xác thực được yêu cầu. Vui lòng kiểm tra cấu hình máy chủ hoặc thử lại sau.",
            "llm_http_429": "Dịch vụ ngôn ngữ đang giới hạn tần suất. Vui lòng thử lại sau ít phút.",
            "llm_http_5xx": "Dịch vụ ngôn ngữ đang gặp sự cố tạm thời. Vui lòng thử lại sau.",
            "llm_http_error": "Dịch vụ ngôn ngữ không xử lý được yêu cầu. Vui lòng thử lại sau.",
            "llm_invalid_json": "Dịch vụ ngôn ngữ trả về dữ liệu không hợp lệ. Vui lòng thử lại; trạng thái chưa bị thay đổi.",
            "llm_schema_validation_failed": "Dịch vụ ngôn ngữ trả về cấu trúc không hợp lệ. Vui lòng thử lại; trạng thái chưa bị thay đổi.",
            "llm_invalid_output": "Tôi chưa hiểu yêu cầu đủ rõ để thực hiện an toàn. Vui lòng nêu hành trình, ngày bay hoặc mã tham chiếu cần dùng.",
            "action_failed": "Không thể hoàn tất yêu cầu này. Vui lòng thử lại với mã theo dõi của yêu cầu.",
            "offer_expired": "Ưu đãi đã hết hạn. Vui lòng tìm lại chuyến bay để nhận giá và chỗ ngồi mới nhất.",
            "provider_unavailable": "Nhà cung cấp chuyến bay đang tạm thời không khả dụng. Vui lòng thử lại sau.",
        }
    else:
        messages = {
            "llm_unavailable": "The language service is temporarily unavailable. Please retry; conversation state was not changed.",
            "llm_timeout": "The language service took too long to respond. Please retry; conversation state was not changed.",
            "llm_http_401_403": "The language service could not authenticate this request. Check the server configuration or retry later.",
            "llm_http_429": "The language service is rate-limiting requests. Please retry in a few minutes.",
            "llm_http_5xx": "The language service is experiencing a temporary problem. Please retry later.",
            "llm_http_error": "The language service could not process this request. Please retry later.",
            "llm_invalid_json": "The language service returned invalid data. Please retry; conversation state was not changed.",
            "llm_schema_validation_failed": "The language service returned an invalid structure. Please retry; conversation state was not changed.",
            "llm_invalid_output": "I could not interpret that request safely. Please provide the route, travel date, or reference ID needed.",
            "action_failed": "This request could not be completed. Please retry with the request trace ID.",
            "offer_expired": "That offer has expired. Please search again for current price and availability.",
            "provider_unavailable": "The flight provider is temporarily unavailable. Please try again later.",
        }
    return messages.get(error, messages["action_failed"])


def _strict_search_command(result: ExecutableFlightSearch) -> SearchFlightsCommand:
    if len(result.destination_airports) != 1:
        raise ValueError("only one destination airport can reach exact search")
    if result.date_window.start_date != result.date_window.end_date:
        raise ValueError("a multi-date window cannot reach exact search")
    return SearchFlightsCommand(
        origin=result.resolved_origin,
        destination=result.destination_airports[0],
        departure_date=result.date_window.start_date,
        passengers=result.passengers,
        cabin=result.cabin,
        currency=result.currency,
        max_stops=result.max_stops,
        baggage_required=result.baggage_required,
        preferred_departure_start=result.preferred_departure_start,
        preferred_departure_end=result.preferred_departure_end,
    )


def _preference_projection(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return TravelPreferencesPlanningProjection.model_validate(value).model_dump(mode="json")
    except (TypeError, ValueError):
        return None


def _planning_safe_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    context: dict[str, Any] = {}
    preference = _preference_projection(value.get("travel_preferences_v1"))
    if preference is not None:
        context["travel_preferences_v1"] = preference
    for key in ("trip_discovery_v1", "presented_offers_v1", "trip_inspiration_v1"):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            context[key] = candidate
    return context


def _safe_planning_messages(value: Any) -> tuple[PlanningMessage, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    sanitized = sanitize_for_llm(list(value)[-24:])
    if not isinstance(sanitized, list):
        return ()
    messages: list[PlanningMessage] = []
    for item in sanitized:
        if not isinstance(item, Mapping):
            continue
        try:
            messages.append(PlanningMessage.model_validate(item))
        except (TypeError, ValueError):
            continue
    return tuple(messages[-24:])


def _planning_pending_clarification(
    safe_context: Any,
    catalog: Any,
) -> PlanningPendingClarification | None:
    if not isinstance(safe_context, Mapping):
        return None
    raw_projection = safe_context.get("trip_discovery_v1")
    if not isinstance(raw_projection, Mapping):
        return None
    raw_pending = raw_projection.get("pending_destination_confirmation")
    if not isinstance(raw_pending, Mapping):
        return None
    try:
        pending = PendingDestinationConfirmation.model_validate(raw_pending)
    except (TypeError, ValueError):
        return None
    reference = catalog.resolve_location(pending.reference.normalized_name)
    if reference.kind is not pending.reference.kind:
        return None
    candidate_id = catalog.planning_candidate_id(reference)
    if candidate_id is None:
        return None
    return PlanningPendingClarification(
        clarification_type="destination",
        candidate_id=candidate_id,
        canonical_name=reference.normalized_name,
    )


def _planning_pending_field(safe_context: Any) -> str | None:
    if not isinstance(safe_context, Mapping):
        return None
    projection = safe_context.get("trip_discovery_v1")
    if not isinstance(projection, Mapping):
        return None
    if (
        isinstance(projection.get("dynamic_destination_choices"), Mapping)
        or isinstance(projection.get("pending_destination_confirmation"), Mapping)
        or projection.get("destination") is None
    ):
        return "destination"
    if projection.get("origin") is None:
        return "origin"
    if projection.get("date_window") is None:
        return "travel_dates"
    return None


def _merge_preference_inputs(command, safe_context: Mapping[str, Any]):
    preference = _preference_projection(safe_context.get("travel_preferences_v1"))
    if preference is None:
        return command, None
    updates: dict[str, Any] = {}
    fields = command.model_fields_set
    if "cabin" not in fields and preference.get("preferred_cabin") is not None:
        updates["cabin"] = preference["preferred_cabin"]
    if "max_stops" not in fields and preference.get("max_stops") is not None:
        updates["max_stops"] = preference["max_stops"]
    if "baggage_required" not in fields and preference.get("baggage_required") is not None:
        updates["baggage_required"] = preference["baggage_required"]
    if (
        "preferred_departure_start" not in fields
        and "preferred_departure_end" not in fields
        and preference.get("preferred_departure_start") is not None
        and preference.get("preferred_departure_end") is not None
    ):
        updates["preferred_departure_start"] = preference["preferred_departure_start"]
        updates["preferred_departure_end"] = preference["preferred_departure_end"]
    if not updates:
        return command, preference
    merged_values = command.model_dump(mode="python")
    merged_values.update(updates)
    return type(command).model_validate(merged_values), preference


def _discovery_safe_result(result: ClarificationRequired) -> dict[str, Any]:
    choices = [choice.model_dump(mode="json") for choice in result.choices]
    return {
        "action": "trip_discovery",
        "status": result.status.value,
        "reason": result.reason.value,
        "missing_fields": list(result.missing_fields),
        "choices": choices,
        "question_vi": result.question_vi,
        "question_en": result.question_en,
        "message_vi": result.question_vi,
        "message_en": result.question_en,
    }


def _discovery_search_safe_result(
    result: TripDiscoverySearchResult,
    *,
    ranking_service: FlightRankingService | None = None,
    ranking_enabled: bool = False,
    now=None,
) -> dict[str, Any]:
    active_ranking_service = ranking_service or FlightRankingService()
    checked_at = now or SystemClock().now()
    requested_currency = result.resolved_request.get("currency")
    max_stops = result.resolved_request.get("max_stops")
    baggage_required = result.resolved_request.get("baggage_required")
    departure_preference = None
    start = result.resolved_request.get("preferred_departure_start")
    end = result.resolved_request.get("preferred_departure_end")
    if isinstance(start, str) and isinstance(end, str):
        from datetime import time

        try:
            departure_preference = (time.fromisoformat(start), time.fromisoformat(end))
        except ValueError:
            departure_preference = None
    departure_timezone = None
    resolved_origin = result.resolved_request.get("resolved_origin")
    date_window = result.resolved_request.get("date_window")
    fallback_timezone = date_window.get("timezone") if isinstance(date_window, dict) else None
    if isinstance(resolved_origin, str):
        departure_timezone = resolve_departure_timezone(
            resolved_origin,
            fallback_timezone=fallback_timezone if isinstance(fallback_timezone, str) else None,
        )
    if ranking_enabled:
        ranked_offers = active_ranking_service.rank(
            result.offers,
            now=checked_at,
            requested_currency=requested_currency,
            max_stops=max_stops,
            baggage_required=baggage_required,
            departure_time_window=departure_preference,
            departure_timezone=departure_timezone,
        )
        offers = [
            safe_offer_response(
                item.offer,
                rank=item.rank,
                ranking_reasons=item.reasons,
            )
            for item in ranked_offers
        ]
        ranking_version = active_ranking_service.ranking_version
    else:
        ordered_offers = provider_order_offers(
            result.offers,
            now=checked_at,
            max_stops=max_stops,
        )
        offers = [
            safe_offer_response(offer, rank=index)
            for index, offer in enumerate(ordered_offers, start=1)
        ]
        ranking_version = "provider-order-v0"

    logger.info(
        "flight_ranking_metric",
        extra={
            "metric_name": "flight_ranking_results_total",
            "version": ranking_version,
            "outcome": result.status.value,
        },
    )
    if result.status.value == "results":
        status = result.status.value
        if ranking_enabled:
            message_vi = (
                f"Tìm thấy {len(offers)} lựa chọn đã xếp hạng theo giá, thời lượng, "
                "số điểm dừng, hành lý và khung giờ nếu có. Giá và chỗ ngồi có thể thay đổi; hãy chọn rõ offer_id hoặc số lựa chọn."
            )
            message_en = (
                f"Found {len(offers)} deterministically ranked options using price, duration, "
                "stops, baggage, and departure fit when provided. Price and availability can change; explicitly choose an offer_id or option number."
            )
        else:
            message_vi = f"Tìm thấy {len(offers)} lựa chọn theo thứ tự nhà cung cấp."
            message_en = f"Found {len(offers)} options in provider order."
    elif result.status.value == "no_results":
        status = "no_results"
        message_vi = "Không tìm thấy lựa chọn còn hiệu lực cho khoảng tìm kiếm này."
        message_en = "No current options matched this search window."
    else:
        status = "provider_unavailable"
        message_vi = "Nhà cung cấp chuyến bay đang tạm thời không khả dụng. Vui lòng thử lại sau."
        message_en = "The flight provider is temporarily unavailable. Please try again later."

    response: dict[str, Any] = {
        "action": "trip_discovery",
        "status": status,
        "persisted": True,
        "discovery_id": str(result.discovery_id),
        "resolved_request": result.resolved_request,
        "attempts": [attempt.model_dump(mode="json") for attempt in result.attempts],
        "ranking_version": ranking_version,
        "returned_results": len(offers),
        "selected_offer_id": None,
        "offers": offers,
        "provider_warnings": list(result.warnings),
        "retryable": result.retryable,
        "trace_id": result.trace_id,
        "message_vi": message_vi,
        "message_en": message_en,
    }
    if offers:
        destinations = result.resolved_request.get("destination_airports", ())
        date_window = result.resolved_request.get("date_window", {})
        response["_recommendation_destination"] = (
            destinations[0] if isinstance(destinations, (list, tuple)) and destinations else None
        )
        response["_recommendation_start_date"] = (
            date_window.get("start_date") if isinstance(date_window, dict) else None
        )
        response["_recommendation_end_date"] = (
            date_window.get("end_date") if isinstance(date_window, dict) else None
        )
    if result.search_id is not None:
        response["search_id"] = str(result.search_id)
        if offers:
            response["_checkpoint_context"] = {
                "presented_offers_v1": {
                    "search_id": str(result.search_id),
                    "expires_at": min(offer["expires_at"] for offer in offers),
                    "offers": [
                        {"rank": offer["rank"], "offer_id": offer["offer_id"]}
                        for offer in offers[:MAX_PRESENTED_OFFERS]
                    ],
                }
            }
    return response


def _discovery_budget_safe_result(
    request: ExecutableFlightSearch,
    error: DiscoveryBudgetExceeded,
) -> dict[str, Any]:
    airport_codes = list(request.destination_airports)
    day_count = (request.date_window.end_date - request.date_window.start_date).days + 1
    airport_label = ", ".join(airport_codes)
    if error.reason == "date_window_too_wide":
        question_vi = "Khoảng ngày hiện quá rộng. Vui lòng chọn một khoảng tối đa bảy ngày."
        question_en = "The date window is too wide. Please choose a range of at most seven days."
    elif len(airport_codes) > 1 and day_count > 1:
        question_vi = (
            f"Phạm vi hiện gồm nhiều sân bay ({airport_label}) trong nhiều ngày và vượt "
            "giới hạn tìm kiếm an toàn. Vui lòng chọn một sân bay hoặc một ngày cụ thể."
        )
        question_en = (
            f"This request covers multiple airports ({airport_label}) across multiple days and "
            "exceeds the safe search limit. Please choose one airport or one specific day."
        )
    elif len(airport_codes) > 1:
        question_vi = (
            f"Phạm vi hiện gồm nhiều sân bay ({airport_label}) và vượt giới hạn tìm kiếm an "
            "toàn. Vui lòng chọn một sân bay cụ thể."
        )
        question_en = (
            f"This request covers multiple airports ({airport_label}) and exceeds the safe "
            "search limit. Please choose one specific airport."
        )
    else:
        question_vi = "Khoảng ngày hiện cần quá nhiều lượt tìm kiếm. Vui lòng chọn một ngày cụ thể."
        question_en = (
            "The date window currently requires too many searches. Please choose one specific day."
        )
    return {
        "action": "trip_discovery",
        "status": "clarification_required",
        "reason": error.reason,
        "missing_fields": list(error.missing_fields),
        "choices": [{"value": code, "label_vi": code, "label_en": code} for code in airport_codes]
        if len(airport_codes) > 1
        else [],
        "question_vi": question_vi,
        "question_en": question_en,
        "message_vi": question_vi,
        "message_en": question_en,
    }


def _ready_safe_result(result: ExecutableFlightSearch) -> dict[str, Any]:
    return {
        "action": "trip_discovery",
        "status": "ready_for_flexible_search",
        "resolved_request": result.model_dump(mode="json"),
        "message_vi": "Đã xác định hành trình và khoảng ngày. Có thể tìm kiếm nhiều ngày khi bật tính năng discovery.",
        "message_en": "The route and date window are resolved. Multi-date search is available when discovery is enabled.",
    }


def _validated_interpreted_destination(
    value: InterpretedLocation | None,
    allowed_locations: tuple[PlanningLocationCandidate, ...],
    catalog: Any,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if value.canonical_query is not None:
        return {
            "canonical_query": value.canonical_query,
            "kind_guess": value.kind_guess,
            "interpretation": value.interpretation,
            "confidence": value.confidence,
            "source_text": sanitize_text(value.source_text[:160]) if value.source_text else None,
        }
    if value.candidate_id is None:
        return None
    allowed = {candidate.candidate_id: candidate for candidate in allowed_locations}
    candidate = allowed.get(value.candidate_id)
    if candidate is None:
        raise ValueError("interpreted destination is not an allowed catalog candidate")
    reference = catalog.resolve_planning_candidate(value.candidate_id)
    if reference is None or reference.kind.value != candidate.kind:
        raise ValueError("interpreted destination candidate is inconsistent with the catalog")
    source_text = sanitize_text(value.source_text.strip()[:160]) if value.source_text else None
    return {
        "reference": reference.model_dump(mode="json"),
        "source_text": source_text,
        "requires_confirmation": value.interpretation != "exact",
    }


def _consistent_planner_destination(
    interpreted: InterpretedLocation | None,
    semantic_destination: Any,
    catalog: Any,
) -> InterpretedLocation | None:
    """Prefer canonical semantics when a local candidate means something else."""

    semantic_query = (
        semantic_destination.place_query
        if semantic_destination is not None
        and semantic_destination.operation not in {"none", "clear"}
        and semantic_destination.mode == "specific"
        else None
    )
    if semantic_query:
        if interpreted is not None and interpreted.candidate_id is not None:
            semantic_reference = catalog.resolve_location(semantic_query)
            if catalog.planning_candidate_id(semantic_reference) == interpreted.candidate_id:
                return interpreted
        elif interpreted is not None and interpreted.canonical_query is not None:
            if normalize_vietnamese_alias(
                interpreted.canonical_query
            ) == normalize_vietnamese_alias(semantic_query):
                return interpreted
        return InterpretedLocation(
            source_text=semantic_destination.source_text,
            canonical_query=semantic_query,
            kind_guess="unknown",
            interpretation="probable",
            confidence=semantic_destination.confidence,
        )

    if interpreted is not None and interpreted.candidate_id is not None and interpreted.source_text:
        source_reference = catalog.resolve_location(interpreted.source_text)
        if catalog.planning_candidate_id(source_reference) != interpreted.candidate_id:
            return InterpretedLocation(
                source_text=interpreted.source_text,
                canonical_query=interpreted.source_text,
                kind_guess="unknown",
                interpretation="probable",
                confidence=interpreted.confidence,
            )
    return interpreted


def _provider_reference(suggestion: LocationSuggestion) -> LocationReference:
    if suggestion.kind is LocationSuggestionKind.AIRPORT:
        return LocationReference(
            kind="airport",
            normalized_name=suggestion.display_name,
            airport_candidates=(suggestion.iata_code,),
            country_code=suggestion.country_code,
        )
    identity = (
        f"{suggestion.country_code}:{suggestion.display_name}:{','.join(suggestion.airport_codes)}"
    )
    city_id = "provider-city-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return LocationReference(
        kind="city",
        normalized_name=suggestion.display_name,
        airport_candidates=suggestion.airport_codes,
        country_code=suggestion.country_code,
        city_id=city_id,
    )


def _dynamic_choices(
    suggestions: tuple[LocationSuggestion, ...],
) -> tuple[DynamicDestinationChoice, ...]:
    choices: list[DynamicDestinationChoice] = []
    for suggestion in suggestions[:8]:
        reference = _provider_reference(suggestion)
        codes = "/".join(reference.airport_candidates)
        label = f"{reference.normalized_name}, {reference.country_code} ({codes})"
        choices.append(
            DynamicDestinationChoice(
                value=reference.city_id or reference.airport_candidates[0],
                label_vi=label,
                label_en=label,
                reference=reference,
            )
        )
    return tuple(choices)


def _dynamic_origin_choices(
    suggestions: tuple[LocationSuggestion, ...],
) -> tuple[DynamicOriginChoice, ...]:
    choices: list[DynamicOriginChoice] = []
    seen_codes: set[str] = set()
    for suggestion in suggestions[:8]:
        base = _provider_reference(suggestion)
        for airport_code in base.airport_candidates:
            if airport_code in seen_codes:
                continue
            seen_codes.add(airport_code)
            reference = LocationReference(
                kind="airport",
                normalized_name=f"{base.normalized_name} ({airport_code})",
                airport_candidates=(airport_code,),
                country_code=base.country_code,
            )
            label = f"{base.normalized_name}, {base.country_code} ({airport_code})"
            choices.append(
                DynamicOriginChoice(
                    value=airport_code,
                    label_vi=label,
                    label_en=label,
                    reference=reference,
                )
            )
            if len(choices) >= 8:
                return tuple(choices)
    return tuple(choices)


def _explicit_option_rank(message: str) -> int | None:
    normalized = normalize_vietnamese_alias(message).strip(" .,!?:;")
    match = re.fullmatch(
        r"(?:option|choice|number|lua chon|phuong an|chon|so)\s*#?\s*(\d{1,2})",
        normalized,
    )
    if match is None:
        match = re.search(
            r"(?:option|choice|number|lua chon|phuong an|chon|so)\s*#?\s*(\d{1,2})",
            normalized,
        )
    if match is None:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 5 else None


def _is_inspiration_search_request(message: str) -> bool:
    normalized = normalize_vietnamese_alias(message)
    return (
        re.search(
            r"(?<!\w)(?:search|find|look up|show|view|tim|tim kiem|tim lai|tra cuu|xem)(?!\w)",
            normalized,
        )
        is not None
    )


def _fallback_conversation_action(message: str) -> str:
    normalized = normalize_vietnamese_alias(message).strip(" .,!?:;")
    if any(
        phrase in normalized
        for phrase in (
            "another destination",
            "different destination",
            "somewhere else",
            "other options",
            "more options",
            "cho nao khac",
            "noi nao khac",
            "cho khac",
            "noi khac",
            "them lua chon",
            "con cho nao",
        )
    ):
        return "request_alternatives"
    if any(
        phrase in normalized
        for phrase in (
            "anywhere is fine",
            "any city",
            "wherever",
            "as long as",
            "dau cung duoc",
            "cho nao cung duoc",
            "noi nao cung duoc",
            "mien la",
        )
    ):
        return "accept_any_destination"
    if normalized in {
        "continue",
        "continue please",
        "go on",
        "keep going",
        "tiep",
        "tiep di",
        "tiep tuc",
    }:
        return "continue_pending"
    return "none"


def _explicit_origin_query(message: str, catalog: AirportCatalog) -> str | None:
    """Extract only an explicitly marked external origin for provider lookup."""

    normalized = normalize_vietnamese_alias(message)
    match = re.search(
        r"(?<!\w)(?:from|tu)\s+(.+?)(?=\s+(?:to|den)\s+|\s*->|$)",
        normalized,
    )
    if match is None:
        return None
    candidate = " ".join(match.group(1).split()).strip(" .,:;!?'\t")
    for stop_term in (
        " next week",
        " this weekend",
        " tomorrow",
        " today",
        " on ",
        " in ",
        " tuan sau",
        " cuoi tuan nay",
        " ngay mai",
        " hom nay",
        " vao ",
        " ngay ",
    ):
        candidate = candidate.split(stop_term, 1)[0]
    candidate = " ".join(candidate.split()).strip(" .,:;!?'")
    if not candidate:
        return None
    try:
        if catalog.resolve_location(candidate).kind.value != "unknown":
            return None
    except (TypeError, ValueError):
        pass
    try:
        return normalize_location_query(candidate)
    except ValueError:
        return None


def build_orchestration_graph(
    llm: LLMProvider,
    action_executor: ActionExecutor | None = None,
    *,
    feature_settings: FeatureSettings | None = None,
    trip_discovery_service: TripDiscoveryService | None = None,
    flight_search_application: FlightSearchApplicationService | None = None,
    destination_recommendations: DestinationRecommendationService | None = None,
    trip_inspiration_service: TripInspirationService | None = None,
    location_resolution_service: LocationResolutionService | None = None,
    clock: Clock | None = None,
    ranking_service: FlightRankingService | None = None,
):
    executor = action_executor or NoopActionExecutor()
    active_features = feature_settings or FeatureSettings()
    active_clock = clock or getattr(executor, "clock", None) or SystemClock()
    active_ranking_service = (
        ranking_service or getattr(executor, "ranking_service", None) or FlightRankingService()
    )
    discovery_service = trip_discovery_service
    if active_features.trip_discovery_enabled and discovery_service is None:
        discovery_service = TripDiscoveryService()
    planning_catalog = (
        getattr(discovery_service, "catalog", None) or AirportCatalog.from_v2_package_data()
    )
    recommendation_service = destination_recommendations or getattr(
        executor, "destination_recommendations", None
    )

    def pending_dynamic_choice_action(state: OrchestrationState):
        safe_context = state.get("safe_context", {})
        projection = (
            safe_context.get("trip_discovery_v1") if isinstance(safe_context, Mapping) else None
        )
        raw_choices = (
            projection.get("dynamic_destination_choices")
            if isinstance(projection, Mapping)
            else None
        )
        return TripDiscoveryService.dynamic_choice_action(
            str(state.get("current_message", "")),
            raw_choices,
            now=active_clock.now(),
        )

    def _origin_choice_selection_requested(message: str) -> bool:
        normalized = normalize_vietnamese_alias(message).strip(" .,!?:;")
        return (
            re.fullmatch(
                r"(?:option|choice|number|lua chon|phuong an|chon|so)\s*#?\s*\d{1,2}",
                normalized,
            )
            is not None
        )

    def pending_dynamic_origin_choice_action(state: OrchestrationState):
        safe_context = state.get("safe_context", {})
        projection = (
            safe_context.get("trip_discovery_v1") if isinstance(safe_context, Mapping) else None
        )
        raw_choices = (
            projection.get("dynamic_origin_choices") if isinstance(projection, Mapping) else None
        )
        action = TripDiscoveryService.dynamic_origin_choice_action(
            str(state.get("current_message", "")),
            raw_choices,
            now=active_clock.now(),
        )
        if (
            action is None
            and raw_choices is not None
            and _origin_choice_selection_requested(str(state.get("current_message", "")))
        ):
            return "repeat"
        return action

    def _authoritative_pending_inspiration_command(
        state: OrchestrationState,
        command: AgentCommand,
    ) -> tuple[AgentCommand, bool]:
        # A trusted inspiration checkpoint may reinterpret only an unclear or
        # discovery classification. Explicit application actions remain first.
        if command.intent not in {AgentIntent.UNCLEAR, AgentIntent.TRIP_DISCOVERY}:
            return command, False
        if not active_features.trip_inspiration_enabled or trip_inspiration_service is None:
            return command, False
        safe_context = state.get("safe_context", {})
        projection = (
            safe_context.get("trip_inspiration_v1") if isinstance(safe_context, Mapping) else None
        )
        if not isinstance(projection, Mapping):
            return command, False
        if not trip_inspiration_service.can_consume_pending_reply(
            str(state.get("current_message", "")),
            projection,
        ):
            return command, False
        return TripInspirationCommand(), True

    def recommendation_eligible(state: OrchestrationState) -> bool:
        if state.get("errors"):
            return False
        if not active_features.destination_recommendations_enabled:
            return False
        result = state.get("safe_result", {})
        if not isinstance(result, dict):
            return False
        action = result.get("action")
        status = result.get("status")
        if action in {"search_flights", "trip_discovery"}:
            return status == "results" and bool(result.get("offers"))
        return action == "start_booking" and status == "draft"

    def recommendation_route(state: OrchestrationState) -> str:
        return "destination_recommender" if recommendation_eligible(state) else "responder"

    def trip_discovery_route(state: OrchestrationState) -> str:
        route = state.get("discovery_route", "clarifier")
        if route == "responder" and recommendation_eligible(state):
            return "destination_recommender"
        return route

    def _safe_recommendation_date(value: object) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    async def destination_recommender(state: OrchestrationState) -> dict[str, Any]:
        result = dict(state.get("safe_result", {}))
        recommendation_input = state.get("destination_recommendation_input", {})
        if not isinstance(recommendation_input, dict):
            recommendation_input = {}
        destination_airport = recommendation_input.get("destination")
        start_date = _safe_recommendation_date(recommendation_input.get("start_date"))
        end_date = _safe_recommendation_date(recommendation_input.get("end_date"))
        interests = recommendation_input.get("interests", ())
        budget_category = recommendation_input.get("budget")
        pace = recommendation_input.get("pace")
        output: dict[str, Any] = {"safe_result": result}
        if recommendation_service is None:
            return output
        if not isinstance(destination_airport, str) or start_date is None:
            return output
        if not isinstance(interests, (list, tuple)) or not all(
            isinstance(item, str) for item in interests
        ):
            interests = ()
        try:
            recommendation = await recommendation_service.recommend(
                destination_airport,
                locale="vi" if state.get("language") == "vi" else "en",
                travel_start_date=start_date,
                travel_end_date=end_date or start_date,
                interests=tuple(interests),
                budget_category=budget_category if isinstance(budget_category, str) else None,
                pace=pace if isinstance(pace, str) else None,
                trace_id=state.get("trace_id"),
            )
            output["safe_result"]["destination_recommendations"] = recommendation.model_dump(
                mode="json"
            )
        except Exception:
            metric_sink = getattr(recommendation_service, "metric_sink", None)
            if metric_sink is not None:
                with suppress(Exception):
                    metric_sink.record(
                        DestinationRecommendationMetric(
                            metric="destination_recommendation_node_errors_total",
                            provider=str(
                                getattr(recommendation_service, "provider_name", "places")
                            ),
                            environment=getattr(
                                recommendation_service,
                                "provider_environment",
                                "mock",
                            ),
                            reason="unexpected_error",
                        )
                    )
            logger.error(
                "destination recommendation node failed",
                extra={
                    "error_code": "destination_recommendation_node_failed",
                    "trace_id": state.get("trace_id"),
                },
            )
        return output

    async def trip_inspiration(state: OrchestrationState) -> dict[str, Any]:
        if trip_inspiration_service is None:
            return {
                "safe_result": {
                    "action": "trip_inspiration",
                    "status": "provider_unavailable",
                    "safe_error_code": "trip_inspiration_not_configured",
                    "retryable": True,
                    "trace_id": state.get("trace_id", "unknown"),
                    "limitations": ["Trip inspiration is not configured."],
                },
                "errors": ["provider_unavailable"],
            }
        try:
            inspiration_kwargs: dict[str, Any] = {
                "message": state["current_message"],
                "locale": "vi" if state.get("language") == "vi" else "en",
                "safe_context": state.get("safe_context", {}),
                "trusted_preferences": _preference_projection(
                    state.get("safe_context", {}).get("travel_preferences_v1")
                    if isinstance(state.get("safe_context"), Mapping)
                    else None
                ),
                "trace_id": state.get("trace_id"),
                "destination_scope": (
                    state.get("destination_scope")
                    if isinstance(state.get("destination_scope"), str)
                    else None
                ),
                "request_alternatives": state.get("conversation_action") == "request_alternatives",
            }
            if active_features.semantic_updates_enabled:
                inspiration_kwargs["semantic_updates"] = state.get("semantic_updates", {})
            result, checkpoint = await trip_inspiration_service.inspire(
                UUID(state["authenticated_user_id"]),
                **inspiration_kwargs,
            )
            safe_context = dict(state.get("safe_context", {}))
            safe_context["trip_inspiration_v1"] = checkpoint.model_dump(mode="json")
            return {
                "safe_result": result.model_dump(mode="json"),
                "safe_context": safe_context,
                "checkpoint_changed": True,
            }
        except Exception:
            logger.error(
                "trip inspiration node failed",
                extra={
                    "error_code": "trip_inspiration_node_failed",
                    "trace_id": state.get("trace_id"),
                },
            )
            return {
                "safe_result": {
                    "action": "trip_inspiration",
                    "status": "provider_unavailable",
                    "safe_error_code": "trip_inspiration_node_failed",
                    "retryable": True,
                    "trace_id": state.get("trace_id", "unknown"),
                    "limitations": ["The inspiration service is temporarily unavailable."],
                },
                "errors": ["provider_unavailable"],
            }

    def initial_route(_state: OrchestrationState) -> str:
        # Every normal conversational turn reaches the configured planner first.
        # Trusted pending choices and confirmations are validated after planning;
        # they no longer bypass the natural-language interpreter.
        return "planner"

    def contextual_planner_fallback(state: OrchestrationState) -> dict[str, Any] | None:
        action = _fallback_conversation_action(str(state.get("current_message", "")))
        safe_context = state.get("safe_context", {})
        discovery_context = (
            safe_context.get("trip_discovery_v1") if isinstance(safe_context, Mapping) else None
        )
        has_dynamic_choices = isinstance(
            discovery_context.get("dynamic_destination_choices")
            if isinstance(discovery_context, Mapping)
            else None,
            Mapping,
        )
        if (
            pending_dynamic_choice_action(state) is not None
            or pending_dynamic_origin_choice_action(state) is not None
            or action in {"accept_any_destination", "continue_pending"}
            and has_dynamic_choices
        ):
            return {
                "command": TripDiscoveryCommand().model_dump(mode="json"),
                "language": _turn_language(
                    str(state.get("current_message", "")),
                    str(state.get("locale", "en")),
                    str(state.get("locale", "en")),
                ),
                "plan": ["validated_contextual_fallback"],
                "dialogue_act": "answer",
                "conversation_action": action,
                "destination_scope": None,
                "interpreted_destination": None,
            }
        inspiration_context = (
            safe_context.get("trip_inspiration_v1") if isinstance(safe_context, Mapping) else None
        )
        if action == "request_alternatives" and isinstance(inspiration_context, Mapping):
            return {
                "command": TripInspirationCommand().model_dump(mode="json"),
                "language": _turn_language(
                    str(state.get("current_message", "")),
                    str(state.get("locale", "en")),
                    str(state.get("locale", "en")),
                ),
                "plan": ["validated_contextual_fallback"],
                "dialogue_act": "answer",
                "conversation_action": action,
                "destination_scope": inspiration_context.get("destination_scope"),
                "interpreted_destination": None,
            }
        return None

    async def deterministic_planner_fallback(state: OrchestrationState) -> dict[str, Any] | None:
        """Use only the bounded offline parser for obvious safe follow-ups."""

        try:
            safe_context = _planning_safe_context(state.get("safe_context", {}))
            allowed_locations = planning_catalog.planning_candidates(limit=100)
            pending = _planning_pending_clarification(
                state.get("safe_context", {}), planning_catalog
            )
            pending_field = _planning_pending_field(state.get("safe_context", {}))
            offline = RuleBasedLLMProvider(
                airports=planning_catalog,
                today=lambda: active_clock.now().date(),
                trip_discovery_enabled=active_features.trip_discovery_enabled,
            )
            plan = await offline.plan(
                PlanningRequest(
                    current_message=str(sanitize_for_llm(state["current_message"])),
                    locale="vi" if state.get("locale") == "vi" else "en",
                    recent_messages=_safe_planning_messages(state.get("recent_messages", ())),
                    safe_summary=None,
                    safe_preferences=safe_context,
                    selected_offer_id=state.get("selected_offer_id"),
                    presented_search_id=(
                        safe_context.get("presented_offers_v1", {}).get("search_id")
                        if isinstance(safe_context.get("presented_offers_v1"), Mapping)
                        else None
                    ),
                    booking_intent_id=state.get("booking_intent_id"),
                    watch_draft_id=state.get("watch_draft_id"),
                    allowed_locations=allowed_locations,
                    pending_clarification=pending,
                    pending_field=pending_field,
                )
            )
            plan = normalize_pending_field_plan(
                plan,
                PlanningRequest(
                    current_message=str(sanitize_for_llm(state["current_message"])),
                    locale="vi" if state.get("locale") == "vi" else "en",
                    safe_preferences=safe_context,
                    pending_field=pending_field,
                ),
            )
            effective = plan.command
            semantic_overrides: dict[str, Any] = {}
            if active_features.semantic_updates_enabled:
                validated = apply_semantic_updates(
                    current_message=state["current_message"],
                    plan=plan,
                    safe_context=state.get("safe_context", {}),
                    clock=active_clock,
                )
                if validated.temporal_window is not None:
                    semantic_overrides["temporal_window"] = validated.temporal_window.model_dump(
                        mode="json"
                    )
                inspiration_context = (
                    safe_context.get("trip_inspiration_v1")
                    if isinstance(safe_context, Mapping)
                    else None
                )
                if (
                    isinstance(inspiration_context, Mapping)
                    and validated.changed_fields
                    and effective.intent in {AgentIntent.UNCLEAR, AgentIntent.TRIP_DISCOVERY}
                ):
                    effective = TripInspirationCommand()
                if validated.referenced_rank is not None:
                    options = (
                        inspiration_context.get("options")
                        if isinstance(inspiration_context, Mapping)
                        else None
                    )
                    if isinstance(options, list):
                        effective = SearchInspirationOptionCommand(
                            option={"rank": validated.referenced_rank}
                        )
            payload = effective.model_dump(mode="json", exclude_unset=True)
            payload["intent"] = effective.intent.value
            if active_features.semantic_updates_enabled:
                logger.info(
                    "semantic_updates_fallback",
                    extra={
                        **_semantic_observability(plan.semantic_updates),
                        "semantic_update_applied": bool(
                            _semantic_observability(plan.semantic_updates)["semantic_update_kind"]
                        ),
                        "semantic_rejection_reason": None,
                        "semantic_clarification_reason": None,
                        "semantic_fallback_used": True,
                        "trace_id": state.get("trace_id"),
                    },
                )
            return {
                "command": payload,
                "language": _turn_language(
                    str(state.get("current_message", "")),
                    plan.language,
                    str(state.get("locale", "en")),
                ),
                "plan": ["bounded_deterministic_fallback", *plan.plan[:10]],
                "dialogue_act": plan.dialogue_act,
                "conversation_action": plan.conversation_action,
                "destination_scope": plan.destination_scope,
                "interpreted_destination": _validated_interpreted_destination(
                    plan.interpreted_destination,
                    allowed_locations,
                    planning_catalog,
                ),
                "semantic_updates": plan.semantic_updates.model_dump(mode="json"),
                "semantic_overrides": semantic_overrides,
            }
        except (LLMOutputError, ValueError, TypeError):
            return None

    async def planner(state: OrchestrationState) -> dict[str, Any]:
        try:
            sanitized_recent = _safe_planning_messages(state.get("recent_messages", ()))
            sanitized_preferences = sanitize_for_llm(
                _planning_safe_context(state.get("safe_context", {}))
            )
            allowed_locations = planning_catalog.planning_candidates(limit=100)
            pending_clarification = _planning_pending_clarification(
                state.get("safe_context", {}), planning_catalog
            )
            pending_field = _planning_pending_field(state.get("safe_context", {}))
            plan = await llm.plan(
                PlanningRequest(
                    current_message=str(sanitize_for_llm(state["current_message"])),
                    locale="vi" if state.get("locale") == "vi" else "en",
                    recent_messages=sanitized_recent,
                    safe_summary=(
                        str(sanitize_for_llm(state["safe_summary"]))
                        if state.get("safe_summary") is not None
                        else None
                    ),
                    safe_preferences=(
                        sanitized_preferences if isinstance(sanitized_preferences, dict) else {}
                    ),
                    selected_offer_id=state.get("selected_offer_id"),
                    presented_search_id=(
                        state.get("safe_context", {})
                        .get("presented_offers_v1", {})
                        .get("search_id")
                        if isinstance(
                            state.get("safe_context", {}).get("presented_offers_v1"), dict
                        )
                        else None
                    ),
                    booking_intent_id=state.get("booking_intent_id"),
                    watch_draft_id=state.get("watch_draft_id"),
                    allowed_locations=allowed_locations,
                    pending_clarification=pending_clarification,
                    pending_field=pending_field,
                )
            )
            plan = normalize_pending_field_plan(
                plan,
                PlanningRequest(
                    current_message=str(sanitize_for_llm(state["current_message"])),
                    locale="vi" if state.get("locale") == "vi" else "en",
                    safe_preferences=(
                        sanitized_preferences if isinstance(sanitized_preferences, dict) else {}
                    ),
                    pending_field=pending_field,
                ),
            )
            validated_semantics: ValidatedSemanticUpdates | None = None
            semantic_updates_payload: dict[str, Any] = {}
            semantic_overrides: dict[str, Any] = {}
            safe_context = state.get("safe_context", {})
            if active_features.semantic_updates_enabled:
                try:
                    preference_context = _preference_projection(
                        state.get("safe_context", {}).get("travel_preferences_v1")
                        if isinstance(state.get("safe_context"), Mapping)
                        else None
                    )
                    validated_semantics = apply_semantic_updates(
                        current_message=state["current_message"],
                        plan=plan,
                        safe_context=state.get("safe_context", {}),
                        clock=active_clock,
                        timezone=(preference_context or {}).get("timezone"),
                    )
                    semantic_updates_payload = plan.semantic_updates.model_dump(mode="json")
                    if validated_semantics.temporal_window is not None:
                        semantic_overrides["temporal_window"] = (
                            validated_semantics.temporal_window.model_dump(mode="json")
                        )
                    if validated_semantics.origin is not None:
                        semantic_overrides["origin"] = validated_semantics.origin.model_dump(
                            mode="json"
                        )
                    if validated_semantics.destination is not None:
                        semantic_overrides["destination"] = (
                            validated_semantics.destination.model_dump(mode="json")
                        )
                    if validated_semantics.search is not None:
                        semantic_overrides["search"] = validated_semantics.search.model_dump(
                            mode="json"
                        )
                    if validated_semantics.budget is not None:
                        semantic_overrides["budget"] = validated_semantics.budget.model_dump(
                            mode="json"
                        )
                    if validated_semantics.passengers is not None:
                        semantic_overrides["passengers"] = (
                            validated_semantics.passengers.model_dump(mode="json")
                        )
                    if validated_semantics.referenced_rank is not None:
                        semantic_overrides["referenced_rank"] = validated_semantics.referenced_rank
                    logger.info(
                        "semantic_updates_evaluated",
                        extra={
                            **_semantic_observability(plan.semantic_updates),
                            "semantic_update_applied": bool(
                                validated_semantics.changed_fields
                                or validated_semantics.referenced_rank is not None
                            ),
                            "semantic_rejection_reason": None,
                            "semantic_clarification_reason": None,
                            "semantic_fallback_used": False,
                            "trace_id": state.get("trace_id"),
                        },
                    )
                except SemanticPolicyRejectionError as exc:
                    logger.info(
                        "semantic_updates_rejected",
                        extra={
                            **_semantic_observability(plan.semantic_updates),
                            "semantic_update_applied": False,
                            "semantic_rejection_reason": exc.reason,
                            "semantic_clarification_reason": exc.reason,
                            "semantic_fallback_used": False,
                            "trace_id": state.get("trace_id"),
                        },
                    )
                    command = UnclearCommand(reason=exc.reason, missing_fields=exc.missing_fields)
                    clarification_context = state.get("safe_context", {})
                    if (
                        exc.reason == "ambiguous_week_anchor"
                        and isinstance(clarification_context, Mapping)
                        and isinstance(clarification_context.get("trip_inspiration_v1"), Mapping)
                    ):
                        clarification_context = dict(clarification_context)
                        inspiration = dict(clarification_context["trip_inspiration_v1"])
                        inspiration["pending_clarification"] = "date_window"
                        inspiration["pending_budget_amount"] = None
                        clarification_context["trip_inspiration_v1"] = inspiration
                    return {
                        "command": command.model_dump(mode="json"),
                        "language": _turn_language(
                            str(state.get("current_message", "")),
                            plan.language,
                            str(state.get("locale", "en")),
                        ),
                        "plan": list(plan.plan),
                        "dialogue_act": plan.dialogue_act,
                        "conversation_action": "none",
                        "destination_scope": None,
                        "clarification_action": None,
                        "interpreted_destination": None,
                        "semantic_updates": {},
                        "semantic_overrides": {},
                        "safe_context": clarification_context,
                        "checkpoint_changed": clarification_context
                        != state.get("safe_context", {}),
                        "safe_result": {
                            "action": "clarify",
                            "status": "clarification_required",
                            "reason": exc.reason,
                            "missing_fields": list(exc.missing_fields),
                            "question_vi": exc.question_vi,
                            "question_en": exc.question_en,
                            "message_vi": exc.question_vi,
                            "message_en": exc.question_en,
                        },
                    }
            effective_command = plan.command
            conversation_action = (
                plan.conversation_action
                if plan.conversation_action != "none"
                else _fallback_conversation_action(state["current_message"])
            )
            if validated_semantics is not None:
                if validated_semantics.result_reference is not None:
                    conversation_action = "reference_presented_result"
                elif (
                    validated_semantics.search is not None
                    and validated_semantics.search.operation != "none"
                ):
                    conversation_action = "refine_search"
                elif validated_semantics.changed_fields:
                    conversation_action = "update_constraints"
            discovery_context = (
                safe_context.get("trip_discovery_v1") if isinstance(safe_context, Mapping) else None
            )
            pending_dynamic_choices = (
                discovery_context.get("dynamic_destination_choices")
                if isinstance(discovery_context, Mapping)
                else None
            )
            trusted_dynamic_action = pending_dynamic_choice_action(state)
            trusted_origin_action = pending_dynamic_origin_choice_action(state)
            if (
                trusted_dynamic_action is not None
                or trusted_origin_action is not None
                or conversation_action in {"accept_any_destination", "continue_pending"}
                and isinstance(pending_dynamic_choices, Mapping)
            ):
                effective_command = TripDiscoveryCommand()
            inspiration_checkpoint = (
                safe_context.get("trip_inspiration_v1")
                if isinstance(safe_context, Mapping)
                else None
            )
            if (
                active_features.semantic_updates_enabled
                and isinstance(inspiration_checkpoint, Mapping)
                and validated_semantics is not None
                and validated_semantics.changed_fields
                and effective_command.intent in {AgentIntent.UNCLEAR, AgentIntent.TRIP_DISCOVERY}
            ):
                # A typed answer to a missing inspiration constraint belongs to
                # the inspiration specialist even when the model has no legacy
                # command vocabulary for that short reply.
                effective_command = TripInspirationCommand()
            if conversation_action == "request_alternatives" and isinstance(
                inspiration_checkpoint, Mapping
            ):
                effective_command = TripInspirationCommand()
            option_rank = (
                validated_semantics.referenced_rank
                if validated_semantics is not None
                and validated_semantics.referenced_rank is not None
                else _explicit_option_rank(state["current_message"])
            )
            safe_context = state.get("safe_context", {})
            presented_context = (
                safe_context.get("presented_offers_v1")
                if isinstance(safe_context, Mapping)
                else None
            )
            if (
                option_rank is not None
                and isinstance(presented_context, Mapping)
                and isinstance(presented_context.get("offers"), list)
                and any(
                    isinstance(item, Mapping) and item.get("rank") == option_rank
                    for item in presented_context["offers"]
                )
            ):
                try:
                    presented_reference = PresentedOfferReference(
                        search_id=presented_context["search_id"],
                        rank=option_rank,
                    )
                except (KeyError, TypeError, ValueError):
                    presented_reference = None
                if presented_reference is not None and (
                    isinstance(effective_command, StartBookingCommand)
                    or (
                        validated_semantics is not None
                        and validated_semantics.result_reference is not None
                    )
                ):
                    effective_command = StartBookingCommand(presented_offer=presented_reference)
            inspiration_context = (
                safe_context.get("trip_inspiration_v1")
                if isinstance(safe_context, Mapping)
                else None
            )
            if (
                option_rank is not None
                and isinstance(inspiration_context, Mapping)
                and isinstance(inspiration_context.get("options"), list)
            ):
                if _is_inspiration_search_request(state["current_message"]):
                    effective_command = SearchInspirationOptionCommand(option={"rank": option_rank})
                elif isinstance(effective_command, StartBookingCommand):
                    effective_command = StartBookingCommand(
                        inspiration_option={"rank": option_rank}
                    )
                elif (
                    validated_semantics is not None
                    and validated_semantics.result_reference is not None
                ):
                    effective_command = SearchInspirationOptionCommand(option={"rank": option_rank})
            if plan.dialogue_act in {"affirm", "reject"}:
                effective_command = (
                    TripDiscoveryCommand()
                    if pending_clarification is not None
                    else UnclearCommand(reason="confirmation_without_pending")
                )
            effective_command, pending_inspiration_override = (
                _authoritative_pending_inspiration_command(state, effective_command)
            )
            command_payload = effective_command.model_dump(mode="json", exclude_unset=True)
            command_payload["intent"] = effective_command.intent.value
            planner_destination = _consistent_planner_destination(
                plan.interpreted_destination,
                validated_semantics.destination if validated_semantics is not None else None,
                planning_catalog,
            )
            interpreted_destination = (
                None
                if pending_inspiration_override
                or conversation_action
                in {
                    "accept_any_destination",
                    "continue_pending",
                    "request_alternatives",
                }
                else _validated_interpreted_destination(
                    planner_destination, allowed_locations, planning_catalog
                )
            )
            semantic_destination_scope = None
            if validated_semantics is not None and validated_semantics.destination is not None:
                destination_update = validated_semantics.destination
                if destination_update.operation not in {"none", "clear"}:
                    semantic_destination_scope = (
                        destination_update.scope_query
                        if destination_update.mode in {"anywhere_within_scope", "domestic_only"}
                        else destination_update.place_query
                        if destination_update.mode == "specific"
                        else None
                    )
            clarification_action = (
                "accept"
                if pending_clarification is not None and plan.dialogue_act == "affirm"
                else "reject"
                if pending_clarification is not None and plan.dialogue_act == "reject"
                else None
            )
            return {
                "command": command_payload,
                "language": _turn_language(
                    str(state.get("current_message", "")),
                    plan.language,
                    str(state.get("locale", "en")),
                ),
                "plan": list(plan.plan),
                "dialogue_act": plan.dialogue_act,
                "conversation_action": conversation_action,
                "destination_scope": plan.destination_scope or semantic_destination_scope,
                "clarification_action": clarification_action,
                "interpreted_destination": interpreted_destination,
                "semantic_updates": semantic_updates_payload,
                "semantic_overrides": semantic_overrides,
            }
        except LLMUnavailableError as exc:
            logger.warning(
                "llm_planner_failed",
                extra={
                    "error_code": exc.safe_code,
                    "trace_id": state.get("trace_id"),
                    "exception_type": type(exc).__name__,
                    "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
                },
            )
            fallback = contextual_planner_fallback(state)
            if fallback is not None:
                return fallback
            if active_features.semantic_updates_enabled:
                fallback = await deterministic_planner_fallback(state)
                if fallback is not None:
                    return fallback
            command = UnclearCommand(reason="language model unavailable")
            return {
                "command": command.model_dump(mode="json"),
                "language": _turn_language(
                    str(state.get("current_message", "")),
                    str(state.get("locale", "en")),
                    str(state.get("locale", "en")),
                ),
                "errors": [exc.safe_code],
            }
        except (LLMOutputError, ValueError, TypeError) as exc:
            logger.warning(
                "llm_planner_failed",
                extra={
                    "error_code": exc.safe_code
                    if isinstance(exc, LLMOutputError)
                    else "llm_invalid_output",
                    "trace_id": state.get("trace_id"),
                    "exception_type": type(exc).__name__,
                    "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
                },
            )
            fallback = contextual_planner_fallback(state)
            if fallback is not None:
                return fallback
            if active_features.semantic_updates_enabled:
                fallback = await deterministic_planner_fallback(state)
                if fallback is not None:
                    return fallback
            command = UnclearCommand(reason="invalid structured plan")
            return {
                "command": command.model_dump(mode="json"),
                "language": _turn_language(
                    str(state.get("current_message", "")),
                    str(state.get("locale", "en")),
                    str(state.get("locale", "en")),
                ),
                "errors": [
                    exc.safe_code if isinstance(exc, LLMOutputError) else "llm_invalid_output"
                ],
            }

    async def context_loader(state: OrchestrationState) -> dict[str, Any]:
        # Re-validating at the action boundary rejects extra or model-invented fields.
        try:
            _command(state)
        except (ValueError, TypeError):
            command = UnclearCommand(reason="invalid command at policy boundary")
            return {
                "command": command.model_dump(mode="json"),
                "errors": ["llm_invalid_output"],
            }
        safe_context = dict(state.get("safe_context", {}))
        safe_context.pop("traveler_profiles", None)
        if active_features.travel_preferences_enabled:
            preference = _preference_projection(safe_context.get("travel_preferences_v1"))
            if preference is None:
                safe_context.pop("travel_preferences_v1", None)
            else:
                safe_context["travel_preferences_v1"] = preference
        else:
            safe_context.pop("travel_preferences_v1", None)
        return {"safe_context": safe_context}

    async def trip_discovery(state: OrchestrationState) -> dict[str, Any]:
        if discovery_service is None:
            return {
                "command": UnclearCommand(reason="trip discovery is disabled").model_dump(
                    mode="json"
                ),
                "errors": ["llm_invalid_output"],
            }
        command = _command(state)
        if not isinstance(command, TripDiscoveryCommand):
            return {
                "command": UnclearCommand(reason="invalid trip discovery command").model_dump(
                    mode="json"
                ),
                "errors": ["llm_invalid_output"],
            }
        # Absolute dates must come from the deterministic resolver. The planner
        # may suggest typed preferences and location spans, but its date fields
        # are not trusted as user facts.
        deterministic_command, preference = _merge_preference_inputs(
            command.model_copy(update={"date_window": None}),
            state.get("safe_context", {}),
        )
        semantic_overrides = state.get("semantic_overrides", {})
        if isinstance(semantic_overrides, Mapping):
            raw_window = semantic_overrides.get("temporal_window")
            if isinstance(raw_window, Mapping):
                deterministic_command = deterministic_command.model_copy(
                    update={"date_window": TravelDateWindow.model_validate(raw_window)}
                )
            raw_search = semantic_overrides.get("search")
            if isinstance(raw_search, Mapping):
                search_updates: dict[str, Any] = {}
                if raw_search.get("direct_only") is True:
                    search_updates["max_stops"] = 0
                if raw_search.get("cabin") is not None:
                    search_updates["cabin"] = raw_search["cabin"]
                if raw_search.get("checked_baggage_required") is not None:
                    search_updates["baggage_required"] = raw_search["checked_baggage_required"]
                if search_updates:
                    deterministic_command = deterministic_command.model_copy(update=search_updates)
            raw_passengers = semantic_overrides.get("passengers")
            if isinstance(raw_passengers, Mapping):
                passenger_values = {
                    "adults": raw_passengers.get("adults")
                    if raw_passengers.get("adults") is not None
                    else deterministic_command.passengers.adults,
                    "children": raw_passengers.get("children")
                    if raw_passengers.get("children") is not None
                    else deterministic_command.passengers.children,
                    "infants": raw_passengers.get("infants")
                    if raw_passengers.get("infants") is not None
                    else deterministic_command.passengers.infants,
                }
                deterministic_command = deterministic_command.model_copy(
                    update={"passengers": passenger_values}
                )
        semantic_reference = None
        semantic_source_text = None
        semantic_requires_confirmation = False
        dynamic_choices: tuple[DynamicDestinationChoice, ...] | None = None
        dynamic_source = None
        dynamic_kind = None
        dynamic_interpretation = "exact"
        dynamic_query = None
        dynamic_origin_reference = None
        dynamic_origin_source = None
        raw_semantic = state.get("interpreted_destination")
        if isinstance(raw_semantic, Mapping):
            try:
                if raw_semantic.get("canonical_query") is not None:
                    dynamic_query = str(raw_semantic["canonical_query"])
                    dynamic_kind = raw_semantic.get("kind_guess")
                    dynamic_interpretation = raw_semantic.get("interpretation", "unknown")
                    semantic_source_text = raw_semantic.get("source_text")
                else:
                    semantic_reference = LocationReference.model_validate(raw_semantic["reference"])
                semantic_source_text = raw_semantic.get("source_text")
                semantic_requires_confirmation = bool(raw_semantic.get("requires_confirmation"))
            except (KeyError, TypeError, ValueError):
                semantic_reference = None
                semantic_source_text = None
                semantic_requires_confirmation = False
                dynamic_query = None
        origin_query = None
        if isinstance(semantic_overrides, Mapping):
            raw_origin = semantic_overrides.get("origin")
            if isinstance(raw_origin, Mapping) and raw_origin.get("mode") == "specific":
                query = raw_origin.get("place_query")
                if isinstance(query, str) and query.strip():
                    origin_query = query.strip()[:160]
        if origin_query is None:
            origin_query = _explicit_origin_query(state["current_message"], planning_catalog)
        if origin_query is not None and active_features.dynamic_location_resolution_enabled:
            if location_resolution_service is None:
                return {
                    "safe_result": {
                        "action": "trip_discovery",
                        "status": "clarification_required",
                        "reason": ClarificationReason.LOCATION_PROVIDER_UNAVAILABLE.value,
                        "missing_fields": ["origin"],
                        "message_vi": "Nguồn tra cứu địa điểm tạm thời không khả dụng. Vui lòng nhập một sân bay khởi hành được hỗ trợ.",
                        "message_en": "Location lookup is temporarily unavailable. Please provide a supported departure airport.",
                    },
                    "discovery_route": "responder",
                    "checkpoint_changed": False,
                }
            try:
                origin_lookup = await location_resolution_service.resolve(
                    LocationLookupRequest(
                        query=origin_query,
                        locale="vi" if state.get("language") == "vi" else "en",
                    ),
                    correlation_id=state.get("trace_id"),
                )
            except ProviderError:
                return {
                    "safe_result": {
                        "action": "trip_discovery",
                        "status": "clarification_required",
                        "reason": ClarificationReason.LOCATION_PROVIDER_UNAVAILABLE.value,
                        "missing_fields": ["origin"],
                        "message_vi": "Nguồn tra cứu địa điểm tạm thời không khả dụng. Vui lòng nhập một sân bay khởi hành được hỗ trợ.",
                        "message_en": "Location lookup is temporarily unavailable. Please provide a supported departure airport.",
                    },
                    "discovery_route": "responder",
                    "checkpoint_changed": False,
                }
            if not origin_lookup.suggestions:
                return {
                    "safe_result": {
                        "action": "trip_discovery",
                        "status": "clarification_required",
                        "reason": ClarificationReason.DYNAMIC_ORIGIN_NOT_FOUND.value,
                        "missing_fields": ["origin"],
                        "message_vi": "Tôi không tìm thấy sân bay hoặc thành phố khởi hành phù hợp. Vui lòng nhập mã sân bay IATA.",
                        "message_en": "I could not find a matching departure city or airport. Please provide an IATA airport code.",
                    },
                    "discovery_route": "responder",
                    "checkpoint_changed": False,
                }
            if origin_lookup.provider not in {"catalog", "duffel", "fixture"}:
                raise ValueError("location provider returned an unsupported source")
            origin_choices = _dynamic_origin_choices(origin_lookup.suggestions)
            if len(origin_choices) != 1:
                choices = [
                    {
                        "value": choice.value,
                        "label_vi": choice.label_vi,
                        "label_en": choice.label_en,
                    }
                    for choice in origin_choices
                ]
                (
                    _prior_origin,
                    prior_destination,
                    prior_dates,
                    prior_confirmation,
                    _prior_origin_source,
                    prior_destination_source,
                    prior_dynamic_destinations,
                    _prior_dynamic_origins,
                ) = TripDiscoveryService._load_projection(state.get("safe_context", {}))
                prior_destination = prior_destination or deterministic_command.destination
                if prior_dates is None:
                    try:
                        prior_dates = discovery_service.date_resolution.resolve(
                            state["current_message"],
                            locale="vi" if state.get("language") == "vi" else "en",
                        )
                    except Exception:
                        prior_dates = None
                pending_origins = DynamicOriginChoices(
                    source=origin_lookup.provider,
                    expires_at=active_clock.now() + timedelta(minutes=30),
                    choices=origin_choices,
                )
                projection = discovery_service._projection(
                    None,
                    prior_destination,
                    prior_dates,
                    prior_confirmation,
                    origin_resolution_source=origin_lookup.provider,
                    destination_resolution_source=prior_destination_source,
                    dynamic_destination_choices=prior_dynamic_destinations,
                    dynamic_origin_choices=pending_origins,
                )
                safe_context = dict(state.get("safe_context", {}))
                safe_context["trip_discovery_v1"] = projection
                return {
                    "safe_result": {
                        "action": "trip_discovery",
                        "status": "clarification_required",
                        "reason": ClarificationReason.MISSING_ORIGIN.value,
                        "missing_fields": ["origin"],
                        "choices": choices,
                        "message_vi": "Vui lòng chọn một sân bay khởi hành cụ thể.",
                        "message_en": "Please choose one specific departure airport.",
                    },
                    "discovery_route": "responder",
                    "safe_context": safe_context,
                    "checkpoint_changed": True,
                }
            dynamic_origin_reference = origin_choices[0].reference
            dynamic_origin_source = origin_lookup.provider
        if dynamic_query is not None and active_features.dynamic_location_resolution_enabled:
            if location_resolution_service is None:
                return {
                    "safe_result": {
                        "action": "trip_discovery",
                        "status": "clarification_required",
                        "reason": ClarificationReason.LOCATION_PROVIDER_UNAVAILABLE.value,
                        "missing_fields": ["destination"],
                        "message_vi": "Nguồn tra cứu địa điểm tạm thời không khả dụng. Vui lòng thử lại hoặc nhập thành phố/mã sân bay.",
                        "message_en": "Location lookup is temporarily unavailable. Please try again or provide a known city or IATA airport code.",
                    },
                    "discovery_route": "responder",
                    "checkpoint_changed": False,
                }
            try:
                lookup = await location_resolution_service.resolve(
                    LocationLookupRequest(
                        query=dynamic_query,
                        locale="vi" if state.get("language") == "vi" else "en",
                    ),
                    correlation_id=state.get("trace_id"),
                )
            except ProviderError:
                return {
                    "safe_result": {
                        "action": "trip_discovery",
                        "status": "clarification_required",
                        "reason": ClarificationReason.LOCATION_PROVIDER_UNAVAILABLE.value,
                        "missing_fields": ["destination"],
                        "message_vi": "Nguồn tra cứu địa điểm tạm thời không khả dụng. Vui lòng thử lại hoặc nhập thành phố/mã sân bay.",
                        "message_en": "Location lookup is temporarily unavailable. Please try again or provide a known city or IATA airport code.",
                    },
                    "discovery_route": "responder",
                    "checkpoint_changed": False,
                }
            if not lookup.suggestions:
                return {
                    "safe_result": {
                        "action": "trip_discovery",
                        "status": "clarification_required",
                        "reason": ClarificationReason.DYNAMIC_DESTINATION_NOT_FOUND.value,
                        "missing_fields": ["destination"],
                        "message_vi": "Tôi không tìm thấy thành phố hoặc sân bay phù hợp. Vui lòng nhập thành phố hoặc mã sân bay IATA.",
                        "message_en": "I could not find a matching city or airport for that destination. Please provide a city or IATA airport code.",
                    },
                    "discovery_route": "responder",
                    "checkpoint_changed": False,
                }
            dynamic_source = lookup.provider
            if dynamic_source not in {"catalog", "duffel", "fixture"}:
                raise ValueError("location provider returned an unsupported source")
            dynamic_choices = _dynamic_choices(lookup.suggestions)
        result, projection = discovery_service.resolve_with_projection(
            deterministic_command,
            message=state["current_message"],
            safe_context=state.get("safe_context", {}),
            trusted_preferences=preference,
            interpreted_origin=dynamic_origin_reference,
            interpreted_origin_source=dynamic_origin_source,
            interpreted_destination=semantic_reference,
            interpreted_source_text=(
                semantic_source_text if isinstance(semantic_source_text, str) else None
            ),
            dynamic_destination_choices=dynamic_choices,
            dynamic_destination_source=dynamic_source,
            dynamic_destination_kind=dynamic_kind,
            dynamic_destination_interpretation=dynamic_interpretation,
            dynamic_destination_query=dynamic_query,
            interpreted_destination_requires_confirmation=semantic_requires_confirmation,
            dynamic_choice_action_override=(
                state.get("conversation_action")
                if state.get("conversation_action")
                in {"accept_any_destination", "continue_pending"}
                else None
            ),
            confirmation_action_override=(
                state.get("clarification_action")
                if state.get("clarification_action") in {"accept", "reject"}
                else None
            ),
            preference_timezone=(preference or {}).get("timezone"),
            locale="vi" if state.get("language") == "vi" else "en",
        )
        safe_context = dict(state.get("safe_context", {}))
        safe_context["trip_discovery_v1"] = projection
        if isinstance(result, ClarificationRequired):
            return {
                "safe_result": _discovery_safe_result(result),
                "discovery_result": result.model_dump(mode="json"),
                "discovery_route": "clarifier",
                "safe_context": safe_context,
                "checkpoint_changed": True,
            }
        if discovery_service.is_single_exact_search(result):
            strict = _strict_search_command(result)
            return {
                "command": strict.model_dump(mode="json"),
                "discovery_result": result.model_dump(mode="json"),
                "discovery_route": "flight_search",
                "trip_discovery_request": True,
                "safe_context": safe_context,
                "checkpoint_changed": True,
            }
        if active_features.flexible_search_enabled and flight_search_application is not None:
            try:
                search_result = await flight_search_application.search_discovery(
                    UUID(state["authenticated_user_id"]),
                    result,
                    trace_id=state.get("trace_id"),
                )
            except DiscoveryBudgetExceeded as exc:
                return {
                    "safe_result": _discovery_budget_safe_result(result, exc),
                    "discovery_result": result.model_dump(mode="json"),
                    "discovery_route": "clarifier",
                    "safe_context": safe_context,
                    "checkpoint_changed": True,
                }
            safe_result = _discovery_search_safe_result(
                search_result,
                ranking_service=active_ranking_service,
                ranking_enabled=active_features.flight_ranking_enabled,
                now=active_clock.now(),
            )
            recommendation_input = {
                key.removeprefix("_recommendation_"): safe_result.pop(key, None)
                for key in (
                    "_recommendation_destination",
                    "_recommendation_start_date",
                    "_recommendation_end_date",
                )
            }
            checkpoint_context = safe_result.pop("_checkpoint_context", None)
            if isinstance(checkpoint_context, dict):
                safe_context.update(checkpoint_context)
            return {
                "safe_result": safe_result,
                "discovery_result": result.model_dump(mode="json"),
                "discovery_route": "responder",
                "trip_discovery_request": True,
                "safe_context": safe_context,
                "destination_recommendation_input": recommendation_input,
                "checkpoint_changed": True,
            }
        return {
            "safe_result": _ready_safe_result(result),
            "discovery_result": result.model_dump(mode="json"),
            "discovery_route": "responder",
            "trip_discovery_request": True,
            "safe_context": safe_context,
            "checkpoint_changed": True,
        }

    def action_node() -> Callable[[OrchestrationState], Awaitable[dict[str, Any]]]:
        async def run(state: OrchestrationState) -> dict[str, Any]:
            try:
                command = _command(state)
                if isinstance(command, SearchFlightsCommand):
                    command, _ = _merge_preference_inputs(command, state.get("safe_context", {}))
                result = dict(await executor.execute(command, state))
                recommendation_input = {
                    key.removeprefix("_recommendation_"): result.pop(key, None)
                    for key in (
                        "_recommendation_destination",
                        "_recommendation_start_date",
                        "_recommendation_end_date",
                    )
                }
                checkpoint_context = result.pop("_checkpoint_context", None)
                output: dict[str, Any] = {
                    "safe_result": result,
                    "selected_offer_id": result.get(
                        "selected_offer_id", state.get("selected_offer_id")
                    ),
                    "booking_intent_id": result.get(
                        "booking_intent_id", state.get("booking_intent_id")
                    ),
                    "watch_draft_id": result.get("watch_draft_id", state.get("watch_draft_id")),
                    "destination_recommendation_input": recommendation_input,
                    "checkpoint_changed": bool(result.get("persisted")),
                }
                if isinstance(checkpoint_context, dict):
                    output["safe_context"] = {
                        **dict(state.get("safe_context", {})),
                        **checkpoint_context,
                    }
                return output
            except Exception as exc:  # executor exceptions are mapped to safe codes
                code = getattr(exc, "safe_code", "action_failed")
                return {"safe_result": {"status": "error"}, "errors": [str(code)]}

        return run

    async def advisor(state: OrchestrationState) -> dict[str, Any]:
        command = _command(state)
        try:
            sanitized_context = sanitize_for_llm(
                _planning_safe_context(state.get("safe_context", {}))
            )
            advice = await llm.advise(
                AdviceRequest(
                    question=str(
                        sanitize_for_llm(getattr(command, "question", state["current_message"]))
                    ),
                    language="vi" if state.get("language") == "vi" else "en",
                    recent_messages=_safe_planning_messages(state.get("recent_messages", ())),
                    safe_summary=(
                        str(sanitize_for_llm(state["safe_summary"]))
                        if state.get("safe_summary") is not None
                        else None
                    ),
                    safe_context=(sanitized_context if isinstance(sanitized_context, dict) else {}),
                )
            )
            return {
                "safe_result": {
                    "action": "advise",
                    "status": "advisory",
                    "message": advice.text,
                    "limitations": list(advice.limitations),
                }
            }
        except (LLMUnavailableError, LLMOutputError):
            return {
                "safe_result": {"action": "advise", "status": "degraded"},
                "errors": ["optional_advice_unavailable"],
            }

    async def clarifier(state: OrchestrationState) -> dict[str, Any]:
        existing = state.get("safe_result", {})
        if existing.get("status") == "clarification_required" and (
            existing.get("question_vi")
            or existing.get("question_en")
            or existing.get("message_vi")
            or existing.get("message_en")
        ):
            return {}
        safe_context = state.get("safe_context", {})
        inspiration_projection = (
            safe_context.get("trip_inspiration_v1") if isinstance(safe_context, Mapping) else None
        )
        if (
            isinstance(inspiration_projection, Mapping)
            and isinstance(inspiration_projection.get("options"), list)
            and inspiration_projection["options"]
        ):
            return {
                "safe_result": {
                    "action": "trip_inspiration",
                    "status": "clarification_required",
                    "missing_fields": ["inspiration_option"],
                    "question_vi": "Bạn muốn chọn phương án nào? Hãy nói rõ, ví dụ: chọn phương án 2.",
                    "question_en": "Which option would you like? Please choose explicitly, for example: Book option 2.",
                    "message_vi": "Tôi chưa tự chọn điểm đến thay bạn. Vui lòng chọn rõ một phương án trong các thẻ kết quả.",
                    "message_en": "I will not choose a destination for you. Please select one of the result cards explicitly.",
                }
            }
        command = _command(state)
        return {
            "safe_result": {
                "action": "clarify",
                "status": "clarification_required",
                "missing_fields": list(getattr(command, "missing_fields", ())),
            }
        }

    async def responder(state: OrchestrationState) -> dict[str, Any]:
        language = "vi" if state.get("language") == "vi" else "en"
        errors = state.get("errors", [])
        blocking = [error for error in errors if error != "optional_advice_unavailable"]
        if blocking:
            return {"final_response": _localized_fallback(language, blocking[0])}
        result = state.get("safe_result", {})
        if result.get("message"):
            return {"final_response": str(result["message"])}
        if result.get("message_vi") and language == "vi":
            return {"final_response": str(result["message_vi"])}
        if result.get("message_en"):
            return {"final_response": str(result["message_en"])}
        if result.get("status") == "no_results":
            if language == "vi":
                return {
                    "final_response": "Tôi chưa tìm thấy kết quả chuyến đi đã xác minh phù hợp với các điều kiện hiện tại. Bạn có thể thử ngày khác hoặc điều chỉnh ngân sách."
                }
            return {
                "final_response": "I couldn't find a verified trip result matching the current constraints. You can try different dates or adjust the budget."
            }
        if result.get("status") == "provider_unavailable":
            if language == "vi":
                return {
                    "final_response": "Dịch vụ chuyến đi hiện tạm thời không khả dụng. Vui lòng thử lại sau."
                }
            return {
                "final_response": "The trip service is temporarily unavailable. Please try again later."
            }
        if result.get("status") == "results":
            count = len(result.get("offers") or result.get("recommendations") or ())
            if language == "vi":
                return {
                    "final_response": f"Tôi đã tìm thấy {count} lựa chọn hiện tại phù hợp. Bạn có thể xem chi tiết bên dưới."
                }
            return {
                "final_response": f"I found {count} current matching options. You can review them below."
            }
        if language == "vi":
            return {
                "final_response": "Vui lòng bổ sung hành trình, ngày bay hoặc mã tham chiếu để tôi hỗ trợ chính xác."
            }
        return {
            "final_response": "Please provide the route, travel date, or reference ID so I can help safely."
        }

    graph = StateGraph(OrchestrationState)
    graph.add_node("planner", planner)
    graph.add_node("context_loader", context_loader)
    graph.add_node("trip_discovery", trip_discovery)
    graph.add_node("flight_search", action_node())
    graph.add_node("inspiration_option_search", action_node())
    graph.add_node("advisor", advisor)
    graph.add_node("booking_coordinator", action_node())
    graph.add_node("watch_coordinator", action_node())
    graph.add_node("profile_coordinator", action_node())
    graph.add_node("destination_recommender", destination_recommender)
    graph.add_node("trip_inspiration", trip_inspiration)
    graph.add_node("clarifier", clarifier)
    graph.add_node("responder", responder)
    graph.add_conditional_edges(START, initial_route)
    graph.add_edge("planner", "context_loader")
    graph.add_conditional_edges(
        "context_loader",
        lambda state: _route(
            state,
            trip_discovery_enabled=active_features.trip_discovery_enabled,
            trip_inspiration_enabled=active_features.trip_inspiration_enabled,
        ),
    )
    graph.add_conditional_edges("trip_discovery", trip_discovery_route)
    graph.add_edge("trip_inspiration", "responder")
    graph.add_conditional_edges("flight_search", recommendation_route)
    graph.add_conditional_edges("inspiration_option_search", recommendation_route)
    graph.add_conditional_edges("booking_coordinator", recommendation_route)
    graph.add_edge("destination_recommender", "responder")
    for node in (
        "advisor",
        "watch_coordinator",
        "profile_coordinator",
        "clarifier",
    ):
        graph.add_edge(node, "responder")
    graph.add_edge("responder", END)
    return graph.compile()
