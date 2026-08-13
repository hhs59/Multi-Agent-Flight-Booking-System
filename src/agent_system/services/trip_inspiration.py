from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from agent_system.domain.exchange_rates import (
    ExchangeRateError,
    ExchangeRateQuote,
    ExchangeRateUnavailableError,
    quantize_currency,
)
from agent_system.domain.flights import CabinClass, FlightSearchCriteria, PassengerMix
from agent_system.domain.location_resolution import (
    LocationLookupRequest,
    LocationSuggestionKind,
)
from agent_system.domain.optimization import (
    OptimizationPreference,
    legacy_sort_preference,
    optimization_preference,
)
from agent_system.domain.trip_discovery import LocationKind, TravelDateWindow
from agent_system.domain.trip_inspiration import (
    BudgetScope,
    DestinationIdea,
    InspirationBudgetComparison,
    TripInspirationCandidateRequest,
    TripInspirationCheckpoint,
    TripInspirationConstraints,
    TripInspirationNoResultReason,
    TripInspirationPendingClarification,
    TripInspirationPresentedOption,
    TripInspirationRecommendation,
    TripInspirationResult,
    TripInspirationStatus,
)
from agent_system.domain.values import CurrencyCode, Money
from agent_system.llm_providers import LLMOutputError, LLMProvider, LLMUnavailableError
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.errors import ProviderError
from agent_system.providers.exchange_rates import ExchangeRateProvider
from agent_system.providers.localization import AirportCatalog, normalize_vietnamese_alias
from agent_system.services.date_resolution import (
    DateResolutionError,
    DateResolutionService,
    TripDiscoverySettings,
)
from agent_system.services.flight_search_application import FlightSearchApplicationService
from agent_system.services.location_resolution import LocationResolutionService
from agent_system.services.semantic_updates import (
    ValidatedSemanticUpdates,
    apply_semantic_updates,
)

logger = logging.getLogger(__name__)


def _canonical_optimization(refinement: object | None) -> OptimizationPreference | None:
    """Normalize current and legacy planner output into one ranking contract."""

    if refinement is None:
        return None
    semantic = getattr(refinement, "optimization", None)
    if semantic is not None:
        return optimization_preference(
            metric=semantic.metric,
            direction=semantic.direction,
            budget_relation=semantic.budget_relation,
        )
    legacy = getattr(refinement, "sort_preference", None)
    if legacy is not None:
        return legacy_sort_preference(legacy)
    return None


def _optimization_note(preference: OptimizationPreference, *, locale: Literal["vi", "en"]) -> str:
    if preference.metric == "fare" and preference.direction == "maximize":
        return (
            " Ưu tiên vé có giá cao nhất nhưng không vượt quá ngân sách vé máy bay."
            if locale == "vi"
            else " I prioritized the highest verified airfare without exceeding your airfare budget."
        )
    if preference.metric == "fare":
        return (
            " Ưu tiên giá vé thấp hơn."
            if locale == "vi"
            else " I prioritized lower verified airfare."
        )
    if preference.metric == "duration":
        return (
            " Ưu tiên thời gian bay ngắn hơn."
            if locale == "vi"
            else " I prioritized shorter flight time."
        )
    if preference.metric == "stops":
        return " Ưu tiên ít điểm dừng hơn." if locale == "vi" else " I prioritized fewer stops."
    if preference.direction == "minimize":
        return (
            " Ưu tiên giờ khởi hành sớm hơn."
            if locale == "vi"
            else " I prioritized earlier departure."
        )
    return (
        " Ưu tiên giờ khởi hành muộn hơn." if locale == "vi" else " I prioritized later departure."
    )


@dataclass(frozen=True)
class TripInspirationSettings:
    max_candidates: int = 5
    max_flight_calls: int = 10
    concurrency: int = 2
    timeout_seconds: float = 30.0
    result_limit: int = 3

    def __post_init__(self) -> None:
        if not 1 <= self.max_candidates <= 8:
            raise ValueError("INSPIRATION_MAX_CANDIDATES must be between 1 and 8")
        if not 1 <= self.max_flight_calls <= 14:
            raise ValueError("INSPIRATION_MAX_FLIGHT_CALLS must be between 1 and 14")
        if not 1 <= self.concurrency <= 3:
            raise ValueError("INSPIRATION_CONCURRENCY must be between 1 and 3")
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("INSPIRATION_TIMEOUT_SECONDS must be greater than zero and at most 60")
        if not 1 <= self.result_limit <= 5:
            raise ValueError("INSPIRATION_RESULT_LIMIT must be between 1 and 5")

    @classmethod
    def from_environment(cls) -> TripInspirationSettings:
        return cls(
            max_candidates=int(os.getenv("INSPIRATION_MAX_CANDIDATES", "5")),
            max_flight_calls=int(os.getenv("INSPIRATION_MAX_FLIGHT_CALLS", "10")),
            concurrency=int(os.getenv("INSPIRATION_CONCURRENCY", "2")),
            timeout_seconds=float(os.getenv("INSPIRATION_TIMEOUT_SECONDS", "30")),
            result_limit=int(os.getenv("INSPIRATION_RESULT_LIMIT", "3")),
        )


@dataclass(frozen=True)
class _ResolvedDestination:
    idea: DestinationIdea
    city: str
    country_code: str
    airport_codes: tuple[str, ...]


@dataclass(frozen=True)
class _IdeaResolution:
    destinations: tuple[_ResolvedDestination, ...]
    provider_failures: bool = False
    had_empty: bool = False
    rejected_places: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SearchJob:
    destination: _ResolvedDestination
    airport_code: str
    departure_date: date


@dataclass(frozen=True)
class _SearchOutcome:
    job: _SearchJob
    result: object | None
    provider_failed: bool = False


@dataclass(frozen=True)
class _RankedOffer:
    destination: _ResolvedDestination
    offer: object
    comparison_amount: Decimal | None
    budget_comparison: InspirationBudgetComparison | None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _trace_id(value: str | None) -> str:
    return str(value or "unknown").strip()[:160] or "unknown"


def _localized(locale: str, vi: str, en: str) -> str:
    return vi if locale == "vi" else en


def _format_money(money: Money | None) -> str:
    if money is None:
        return "the requested budget"
    amount = format(money.amount, ",f")
    if "." in amount:
        amount = amount.rstrip("0").rstrip(".")
    return f"{amount} {money.currency}"


def _no_result_messages(
    reason: TripInspirationNoResultReason,
    *,
    origin_label: str | None,
    airfare_budget: Money | None,
) -> tuple[str, str]:
    origin_vi = origin_label or "điểm khởi hành"
    origin_en = origin_label or "your departure city"
    budget = _format_money(airfare_budget)
    messages = {
        TripInspirationNoResultReason.OVER_BUDGET: (
            f"Tôi chưa tìm thấy vé từ {origin_vi} trong khoảng ngày đã chọn có giá không quá {budget}. Bạn có thể thử ngày khác hoặc tăng ngân sách.",
            f"I couldn't find flights from {origin_en} in the selected date window at or below {budget}. You can try different dates or increase your budget.",
        ),
        TripInspirationNoResultReason.CURRENCY_MISMATCH: (
            f"Tôi chưa tìm thấy vé hiển thị bằng {airfare_budget.currency if airfare_budget else 'loại tiền đã chọn'} trong khoảng ngày đã chọn. Bạn có thể thử loại tiền khác hoặc ngày khác.",
            f"I couldn't find flights quoted in {airfare_budget.currency if airfare_budget else 'the requested currency'} in the selected date window. You can try another currency or different dates.",
        ),
        TripInspirationNoResultReason.CURRENCY_CONVERSION_UNAVAILABLE: (
            "Tôi đã tìm thấy vé nhưng chưa thể quy đổi an toàn giữa các loại tiền để so sánh với ngân sách. Không có lựa chọn chưa xác minh nào được hiển thị; bạn có thể thử lại sau.",
            "I found fares, but could not safely convert their currencies to compare them with your budget. No unverified option was shown; please try again later.",
        ),
        TripInspirationNoResultReason.CANDIDATE_GENERATION_EMPTY: (
            "Tôi chưa tạo được gợi ý điểm đến phù hợp với các điều kiện hiện tại. Bạn có thể thử thay đổi ngày hoặc ngân sách.",
            "I couldn't generate a suitable destination suggestion for the current constraints. You can try different dates or a different budget.",
        ),
        TripInspirationNoResultReason.CANDIDATE_VALIDATION_FAILED: (
            "Tôi chưa thể xác minh các thành phố được gợi ý thành sân bay cụ thể. Tôi đã giữ lại điểm khởi hành, ngày và ngân sách để bạn thử lại.",
            "I couldn't validate the suggested cities as specific airports. I kept your origin, dates, and budget so you can try again.",
        ),
        TripInspirationNoResultReason.SEARCH_BUDGET_EXHAUSTED: (
            "Tôi đã đạt giới hạn kiểm tra an toàn trước khi tìm thấy vé đã xác minh. Bạn có thể thu hẹp khoảng ngày hoặc thử lại.",
            "I reached the safe search limit before finding a verified flight. You can narrow the date window or try again.",
        ),
        TripInspirationNoResultReason.NO_VERIFIED_OFFER: (
            "Tôi chưa tìm thấy giá vé hiện tại nào có thể xác minh cho các điểm đến phù hợp trong khoảng ngày đã chọn.",
            "I couldn't find a current airfare price that I could verify for the suitable destinations in the selected date window.",
        ),
    }
    return messages[reason]


def _log_inspiration_metrics(
    trace_id: str,
    *,
    candidate_ideas_count: int,
    candidate_validated_count: int,
    candidate_rejected_count: int,
    repair_attempted: bool,
    country_count: int,
    jobs_scheduled: int,
    call_budget: int,
    offers_seen: int,
    offers_over_budget: int,
    offers_currency_mismatch: int,
    offers_converted: int = 0,
    offers_fx_unavailable: int = 0,
    offers_fx_invalid_or_expired: int = 0,
    no_result_reason: TripInspirationNoResultReason | None = None,
) -> None:
    logger.info(
        "trip_inspiration_metrics",
        extra={
            "trace_id": trace_id,
            "candidate_ideas_count": candidate_ideas_count,
            "candidate_validated_count": candidate_validated_count,
            "candidate_rejected_count_by_reason": {
                "location_validation": candidate_rejected_count,
            },
            "candidate_repair_attempted": repair_attempted,
            "candidate_country_count": country_count,
            "flight_jobs_scheduled": jobs_scheduled,
            "flight_call_budget": call_budget,
            "offers_seen": offers_seen,
            "offers_over_budget": offers_over_budget,
            "offers_currency_mismatch": offers_currency_mismatch,
            "offers_converted": offers_converted,
            "offers_fx_unavailable": offers_fx_unavailable,
            "offers_fx_invalid_or_expired": offers_fx_invalid_or_expired,
            "no_result_reason": no_result_reason.value if no_result_reason else None,
        },
    )


def _extract_origin_query(message: str) -> str | None:
    normalized = normalize_vietnamese_alias(message)
    match = re.search(
        r"(?<!\w)(?:from|tu)\s+(.+?)(?=\s+(?:to|den)\s+|\s*->|$)",
        normalized,
    )
    if match is None:
        return None
    candidate = " ".join(match.group(1).split()).strip(" .,:;!?'")
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
    candidate = " ".join(candidate.split()).strip(" .,:;!?'\t")
    return candidate[:160] or None


def _extract_bare_origin_query(message: str) -> str | None:
    candidate = " ".join(normalize_vietnamese_alias(message).split()).strip(" .,:;!?")
    if not candidate or len(candidate) > 160 or len(candidate.split()) > 6:
        return None
    if re.search(r"\d", candidate) and not re.fullmatch(r"[a-z]{3}", candidate):
        return None
    return candidate


def _extract_return_date(message: str) -> date | None:
    match = re.search(
        r"(?<!\w)(?:return|back|quay lai|ve lai)(?:\s+on|\s+ngay)?\s+([0-9]{4}-[0-9]{1,2}-[0-9]{1,2}|[0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{4})",
        normalize_vietnamese_alias(message),
    )
    if match is None:
        return None
    raw = match.group(1)
    try:
        if "-" in raw and raw.startswith("20"):
            return date.fromisoformat(raw)
        day, month, year = map(int, re.split(r"[/-]", raw))
        return date(year, month, day)
    except ValueError:
        return None


_CURRENCY_ALIASES: tuple[tuple[str, str], ...] = (
    ("usd", "USD"),
    ("dollars", "USD"),
    ("dollar", "USD"),
    ("do la", "USD"),
    ("do", "USD"),
    ("vnd", "VND"),
    ("dong", "VND"),
    ("eur", "EUR"),
    ("euro", "EUR"),
    ("gbp", "GBP"),
    ("jpy", "JPY"),
    ("aud", "AUD"),
    ("sgd", "SGD"),
    ("thb", "THB"),
)

_BUDGET_SIGNAL = re.compile(
    r"[$€£]|\b(?:budget|spend|have|under|less than|for flights|airfare|flight budget|"
    r"usd|vnd|eur|gbp|jpy|aud|sgd|thb|trieu|million|tr|m|nghin|ngan|k|cu|ngan sach|co|duoi|tien ve|ve may bay)\b"
)
_DATE_NUMBER = re.compile(
    r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|"
    r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"
)
_AMOUNT = re.compile(r"(?<![\w+-])((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)(?!\w)")
_NEGATIVE_AMOUNT = re.compile(r"(?<!\w)-\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?(?!\w)")
_MILLION_AMOUNT = re.compile(r"(?<![\w+-])(\d+(?:[.,]\d{1,2})?)\s*(?:trieu|million)(?!\w)")
_SHORT_MILLION_AMOUNT = re.compile(r"(?<![\w+-])(\d+(?:[.,]\d{1,2})?)\s*(?:tr|m)(?!\w)")
_THOUSAND_AMOUNT = re.compile(r"(?<![\w+-])(\d+(?:[.,]\d{1,2})?)\s*(?:nghin|ngan|k)(?!\w)")
_CURRENCY_TOKEN = r"(?:[$\u20ac\u00a3]|(?<!\w)(?:usd|dollars?|do la|do|vnd|dong|eur|euro|gbp|jpy|aud|sgd|thb)(?!\w))"
_BUDGET_NUMBER_PREFIX = re.compile(
    r"(?:\b(?:budget|spend|have|under|less than|airfare|flight budget|for flights|"
    r"ngan sach|co|duoi|tien ve|ve may bay)\b)\s*(?:is|la|of)?\s*[:=,-]?\s*$"
)
_BUDGET_NUMBER_SUFFIX = re.compile(
    r"^\s*(?:for\s+(?:airfare|flights?|tickets?)|(?:airfare|flight)\s+budget)\b"
)


def _extract_currency(message: str) -> CurrencyCode | None:
    normalized = normalize_vietnamese_alias(message)
    if "$" in message:
        return "USD"
    if "€" in message:
        return "EUR"
    if "£" in message:
        return "GBP"
    if re.search(r"(?<!\w)trieu(?!\w)", normalized):
        return "VND"
    if re.search(
        r"(?<!\w)\d+(?:[.,]\d+)?\s*(?:tr|m|nghin|ngan|k|cu)(?!\w)",
        normalized,
    ):
        return "VND"
    for alias, code in _CURRENCY_ALIASES:
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
            return code  # type: ignore[return-value]
    return None


def _without_passenger_counts(normalized: str) -> str:
    value = normalized
    for pattern, _field in _PARTY_PATTERNS:
        value = re.sub(pattern, " ", value)
    return value


def _extract_budget_amount(
    message: str, *, allow_bare: bool = False
) -> tuple[Decimal | None, bool]:
    normalized = normalize_vietnamese_alias(message)
    has_budget_signal = _BUDGET_SIGNAL.search(normalized) is not None
    million_match = _MILLION_AMOUNT.search(normalized)
    if million_match is None and has_budget_signal:
        million_match = _SHORT_MILLION_AMOUNT.search(normalized)
    if million_match is not None:
        try:
            amount = Decimal(million_match.group(1).replace(",", ".")) * Decimal("1000000")
        except InvalidOperation:
            return None, True
        return (amount, True) if amount.is_finite() and amount > 0 else (None, True)
    thousand_match = _THOUSAND_AMOUNT.search(normalized)
    if thousand_match is not None:
        try:
            amount = Decimal(thousand_match.group(1).replace(",", ".")) * Decimal("1000")
        except InvalidOperation:
            return None, True
        return (amount, True) if amount.is_finite() and amount > 0 else (None, True)
    if not allow_bare and not has_budget_signal:
        return None, False
    amount_text = _without_passenger_counts(_DATE_NUMBER.sub(" ", normalized))
    if _NEGATIVE_AMOUNT.search(amount_text) is not None:
        return None, True

    candidates = []
    for match in _AMOUNT.finditer(amount_text):
        prefix = amount_text[max(0, match.start() - 48) : match.start()]
        suffix = amount_text[match.end() : min(len(amount_text), match.end() + 48)]
        currency_associated = bool(
            re.search(rf"{_CURRENCY_TOKEN}\s*$", prefix)
            or re.match(rf"\s*{_CURRENCY_TOKEN}(?!\w)", suffix)
        )
        phrase_associated = bool(
            _BUDGET_NUMBER_PREFIX.search(prefix) or _BUDGET_NUMBER_SUFFIX.match(suffix)
        )
        candidates.append((match, currency_associated, phrase_associated))
    if not candidates:
        return None, False

    preferred = [candidate for candidate in candidates if candidate[1]]
    if not preferred:
        preferred = [candidate for candidate in candidates if candidate[2]]
    if not preferred and allow_bare and len(candidates) == 1:
        preferred = candidates
    if len(preferred) != 1:
        # Multiple unrelated numbers are unsafe to interpret as money. The caller
        # keeps the state machine in clarification instead of selecting the first.
        return None, True

    try:
        amount = Decimal(preferred[0][0].group(1).replace(",", ""))
    except InvalidOperation:
        return None, True
    if not amount.is_finite() or amount <= 0:
        return None, True
    return amount, True


def _extract_budget_scope(message: str) -> BudgetScope:
    normalized = normalize_vietnamese_alias(message)
    if re.search(
        r"(?:whole trip|entire trip|all trip|total trip|everything|ca chuyen|"
        r"toan bo chuyen|ca hanh trinh)",
        normalized,
    ):
        return BudgetScope.TOTAL_TRIP
    if re.search(
        r"(?:airfare only|flight only|flights?|flight tickets?|plane tickets?|"
        r"airfare|tickets?|ve may bay|tien ve|chi ve|chi phi ve|chi ve may bay)",
        normalized,
    ):
        return BudgetScope.AIRFARE_ONLY
    return BudgetScope.UNKNOWN


def _uses_all_total_budget_for_airfare(message: str) -> bool:
    """Recognize a scoped request to reuse the saved whole-trip amount for flights."""

    normalized = normalize_vietnamese_alias(message)
    has_all = (
        re.search(
            r"(?<!\w)(?:all|entire|whole|everything|full|toan bo|tat ca|chuyen het|dung het)(?!\w)",
            normalized,
        )
        is not None
    )
    has_airfare = (
        re.search(
            r"(?<!\w)(?:airfare|flights?|flight tickets?|plane tickets?|tickets?|ve may bay|tien ve)(?!\w)",
            normalized,
        )
        is not None
    )
    return has_all and has_airfare


def _extract_budget(message: str) -> tuple[Decimal | None, CurrencyCode | None, bool]:
    amount, mentioned = _extract_budget_amount(message)
    return amount, _extract_currency(message), mentioned


_PARTY_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"(?<!\w)(\d{1,2})\s*(?:adults?|adult passengers?|passengers?|people|nguoi lon|nguoi)(?!\w)",
        "adults",
    ),
    (r"(?<!\w)(\d{1,2})\s*(?:child(?:ren)?|kids?|tre em)(?!\w)", "children"),
    (r"(?<!\w)(\d{1,2})\s*(?:infants?|babies?|baby|em be)(?!\w)", "infants"),
)


def _is_explicit_option_selection(message: str) -> bool:
    normalized = normalize_vietnamese_alias(message)
    return (
        re.search(
            r"(?<!\w)(?:option|choice|number|lua chon|phuong an|chon|so)\s*#?\s*\d{1,2}(?!\w)",
            normalized,
        )
        is not None
    )


def _extract_passengers(
    message: str,
    prior: PassengerMix,
) -> tuple[PassengerMix, bool, str | None]:
    normalized = normalize_vietnamese_alias(message)
    values = {
        "adults": prior.adults,
        "children": prior.children,
        "infants": prior.infants,
    }
    found = False
    for pattern, field in _PARTY_PATTERNS:
        match = re.search(pattern, normalized)
        if match is not None:
            values[field] = int(match.group(1))
            found = True
    if not found:
        return prior, False, None
    try:
        return PassengerMix(**values), True, None
    except ValueError as exc:
        return prior, True, str(exc)


def can_consume_pending_reply(
    message: str,
    checkpoint: TripInspirationCheckpoint | Mapping[str, object],
) -> bool:
    """Return whether a message can deterministically answer a trusted checkpoint field."""

    try:
        validated = (
            checkpoint
            if isinstance(checkpoint, TripInspirationCheckpoint)
            else TripInspirationCheckpoint.model_validate(checkpoint)
        )
    except (TypeError, ValueError):
        return False
    pending = validated.pending_clarification
    normalized_message = normalize_vietnamese_alias(message)
    if pending is None or _is_explicit_option_selection(message):
        return False
    if re.search(
        r"(?<!\w)(?:book|booking|reserve|confirm|payment|pay|cancel|refund|"
        r"watch|notify|alert|profile|traveler|auto buy|dat ve|giu cho|"
        r"xac nhan|thanh toan|huy|hoan ve|theo doi|canh bao|ho so|"
        r"thong tin hanh khach)(?!\w)",
        normalized_message,
    ):
        return False
    if pending is TripInspirationPendingClarification.ORIGIN:
        if _extract_origin_query(message) is not None:
            return True
        bare = _extract_bare_origin_query(message)
        if bare is None:
            return False
        normalized = normalize_vietnamese_alias(bare)
        if normalized in {
            "ok",
            "yes",
            "no",
            "maybe",
            "next week",
            "this weekend",
            "tomorrow",
            "today",
            "tuan sau",
            "cuoi tuan nay",
            "ngay mai",
            "hom nay",
        }:
            return False
        if _extract_budget_scope(message) is not BudgetScope.UNKNOWN:
            return False
        if _extract_currency(message) is not None:
            return False
        return not _extract_passengers(message, validated.passengers)[1]
    if pending is TripInspirationPendingClarification.DATE_WINDOW:
        return bool(
            re.search(
                r"(?<!\w)(?:today|tomorrow|tonight|this week|next week|this weekend|next weekend|"
                r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                r"any day of the week|any day in the week|"
                r"ngay nao trong tuan|bat cu ngay nao trong tuan|"
                r"hom nay|ngay mai|tuan nay|tuan sau|cuoi tuan nay|cuoi tuan sau|"
                r"thu hai|thu ba|thu tu|thu nam|thu sau|thu bay|chu nhat|"
                r"\d{1,2}[/-]\d{1,2}(?:[/-]20\d{2})?|20\d{2}[/-]\d{1,2}[/-]\d{1,2})(?!\w)",
                normalized_message,
            )
        )
    if pending is TripInspirationPendingClarification.BUDGET_CURRENCY:
        return _extract_currency(message) is not None
    if pending is TripInspirationPendingClarification.BUDGET_SCOPE:
        # Older checkpoints may still contain this pending value. The current
        # default is airfare-only, so any safe reply can resume the trip flow.
        return True
    if pending is TripInspirationPendingClarification.AIRFARE_ALLOCATION:
        if validated.total_trip_budget is not None and _uses_all_total_budget_for_airfare(message):
            return True
        amount, mentioned = _extract_budget_amount(message, allow_bare=True)
        return mentioned and amount is not None
    if pending is TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY:
        return _extract_currency(message) is not None
    if pending is TripInspirationPendingClarification.PASSENGERS:
        return _extract_passengers(message, validated.passengers)[1]
    return False


_CABIN_PATTERNS: tuple[tuple[str, CabinClass], ...] = (
    (r"(?:premium\s+economy|premium economy|pho thong cao cap)", CabinClass.PREMIUM_ECONOMY),
    (r"(?:business(?:\s+class)?|thuong gia)", CabinClass.BUSINESS),
    (r"(?:first(?:\s+class)?|hang nhat)", CabinClass.FIRST),
    (r"(?:economy(?:\s+class)?|coach|pho thong)", CabinClass.ECONOMY),
)


def _extract_cabin(message: str) -> CabinClass | None:
    normalized = normalize_vietnamese_alias(message)
    for pattern, cabin in _CABIN_PATTERNS:
        if re.search(rf"(?<!\w){pattern}(?!\w)", normalized):
            return cabin
    return None


def _extract_interests(message: str) -> tuple[str, ...]:
    normalized = normalize_vietnamese_alias(message)
    allowed = (
        ("beach", "beach"),
        ("sea", "beach"),
        ("bien", "beach"),
        ("food", "food"),
        ("cuisine", "food"),
        ("am thuc", "food"),
        ("culture", "culture"),
        ("van hoa", "culture"),
        ("nature", "nature"),
        ("thien nhien", "nature"),
        ("shopping", "shopping"),
        ("mua sam", "shopping"),
        ("history", "history"),
        ("lich su", "history"),
    )
    found: list[str] = []
    for phrase, value in allowed:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized) and value not in found:
            found.append(value)
    return tuple(found[:5])


class TripInspirationService:
    """Generate bounded destination ideas, then verify them with persisted flight searches."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        location_resolution: LocationResolutionService | None,
        flight_search_application: FlightSearchApplicationService,
        clock: Clock | None = None,
        settings: TripInspirationSettings | None = None,
        catalog: AirportCatalog | None = None,
        date_settings: TripDiscoverySettings | None = None,
        exchange_rates: ExchangeRateProvider | None = None,
    ) -> None:
        self.llm = llm
        self.location_resolution = location_resolution
        self.flight_search_application = flight_search_application
        self.exchange_rates = exchange_rates
        self.clock = clock or SystemClock()
        self.settings = settings or TripInspirationSettings.from_environment()
        self.catalog = catalog or AirportCatalog.from_v2_package_data()
        active_date_settings = date_settings or TripDiscoverySettings()
        self.date_resolution = DateResolutionService(
            clock=self.clock,
            timezone=active_date_settings.default_timezone,
        )

    @staticmethod
    def _checkpoint(value: object) -> TripInspirationCheckpoint | None:
        if not isinstance(value, Mapping):
            return None
        try:
            return TripInspirationCheckpoint.model_validate(value)
        except (TypeError, ValueError):
            return None

    def _prior_checkpoint(
        self, safe_context: Mapping[str, object] | None
    ) -> TripInspirationCheckpoint | None:
        if not isinstance(safe_context, Mapping):
            return None
        return self._checkpoint(safe_context.get("trip_inspiration_v1"))

    def can_consume_pending_reply(
        self,
        message: str,
        checkpoint: TripInspirationCheckpoint | Mapping[str, object],
    ) -> bool:
        return can_consume_pending_reply(message, checkpoint)

    def _make_checkpoint(
        self,
        *,
        origin: str | None,
        date_window: TravelDateWindow | None,
        return_date: date | None,
        airfare_budget: Money | None,
        total_trip_budget: Money | None,
        budget_scope: BudgetScope,
        interests: tuple[str, ...],
        budget_allocation: str = "unknown",
        optimization: OptimizationPreference | None = None,
        destination_scope: str | None = None,
        excluded_destinations: tuple[str, ...] = (),
        options: tuple[TripInspirationPresentedOption, ...] = (),
        passengers: PassengerMix | None = None,
        cabin: CabinClass = CabinClass.ECONOMY,
        pending_clarification: TripInspirationPendingClarification | None = None,
        pending_budget_amount: Decimal | None = None,
    ) -> TripInspirationCheckpoint:
        expiry = min((option.expires_at for option in options), default=None)
        return TripInspirationCheckpoint(
            origin=origin,
            date_window=date_window,
            return_date=return_date,
            airfare_budget=airfare_budget,
            total_trip_budget=total_trip_budget,
            budget_scope=budget_scope,
            budget_allocation=budget_allocation,
            optimization=optimization,
            passengers=passengers or PassengerMix(),
            cabin=cabin,
            interests=interests,
            destination_scope=destination_scope,
            excluded_destinations=excluded_destinations,
            options=options,
            expires_at=expiry,
            pending_clarification=pending_clarification,
            pending_budget_amount=pending_budget_amount,
        )

    def _result_constraints(
        self,
        *,
        origin: str | None,
        date_window: TravelDateWindow | None,
        return_date: date | None,
        airfare_budget: Money | None,
        budget_scope: BudgetScope,
        passengers,
        cabin,
        interests: tuple[str, ...],
        budget_allocation: str = "unknown",
        optimization: OptimizationPreference | None = None,
    ) -> TripInspirationConstraints:
        return TripInspirationConstraints(
            origin=origin,
            date_window=date_window,
            return_date=return_date,
            airfare_budget=airfare_budget,
            budget_scope=budget_scope,
            budget_allocation=budget_allocation,
            optimization=optimization,
            passengers=passengers,
            cabin=cabin,
            interests=interests,
        )

    def _clarification(
        self,
        *,
        locale: Literal["vi", "en"],
        trace_id: str,
        missing_fields: tuple[str, ...],
        question_vi: str,
        question_en: str,
        constraints: TripInspirationConstraints,
        checkpoint: TripInspirationCheckpoint,
        message_vi: str | None = None,
        message_en: str | None = None,
    ) -> tuple[TripInspirationResult, TripInspirationCheckpoint]:
        del locale
        message_vi = message_vi or question_vi
        message_en = message_en or question_en
        return (
            TripInspirationResult(
                status=TripInspirationStatus.CLARIFICATION_REQUIRED,
                constraints=constraints,
                missing_fields=missing_fields,
                question_vi=question_vi,
                question_en=question_en,
                message_vi=message_vi,
                message_en=message_en,
                trace_id=trace_id,
            ),
            checkpoint,
        )

    def _no_results(
        self,
        *,
        reason: TripInspirationNoResultReason,
        constraints: TripInspirationConstraints,
        trace_id: str,
        origin_label: str | None,
        airfare_budget: Money | None,
        limitations: tuple[str, ...] = (),
        checkpoint: TripInspirationCheckpoint,
    ) -> tuple[TripInspirationResult, TripInspirationCheckpoint]:
        message_vi, message_en = _no_result_messages(
            reason,
            origin_label=origin_label,
            airfare_budget=airfare_budget,
        )
        logger.info(
            "trip_inspiration_no_result",
            extra={"trace_id": trace_id, "no_result_reason": reason.value},
        )
        return (
            TripInspirationResult(
                status=TripInspirationStatus.NO_RESULTS,
                constraints=constraints,
                limitations=limitations,
                message_vi=message_vi,
                message_en=message_en,
                no_result_reason=reason,
                safe_error_code=(
                    "currency_conversion_unavailable"
                    if reason is TripInspirationNoResultReason.CURRENCY_CONVERSION_UNAVAILABLE
                    else None
                ),
                retryable=(reason is TripInspirationNoResultReason.CURRENCY_CONVERSION_UNAVAILABLE),
                trace_id=trace_id,
            ),
            checkpoint,
        )

    async def _resolve_origin(
        self,
        query: str,
        *,
        locale: Literal["vi", "en"],
        trace_id: str,
    ) -> tuple[str | None, str | None, str | None]:
        try:
            reference = self.catalog.resolve_location(query)
        except (TypeError, ValueError):
            reference = None
        if reference is not None and reference.kind in {LocationKind.CITY, LocationKind.AIRPORT}:
            if len(reference.airport_candidates) == 1:
                airport = self.catalog.get(reference.airport_candidates[0])
                label = airport.city_name_vi if locale == "vi" else airport.city_name_en
                return reference.airport_candidates[0], label, "catalog"
            if reference.kind is LocationKind.AIRPORT:
                return None, None, "catalog"
        if reference is not None and reference.kind is LocationKind.COUNTRY:
            return None, None, "catalog"
        if self.location_resolution is None:
            raise ProviderError(
                provider="locations",
                operation="location_suggestions",
                safe_message="location resolution is not configured",
                retryable=False,
            )
        lookup = await self.location_resolution.resolve(
            LocationLookupRequest(
                query=query,
                locale=locale,
                limit=8,
            ),
            correlation_id=trace_id,
        )
        if not lookup.suggestions:
            return None, None, lookup.provider
        if len(lookup.suggestions) != 1:
            return None, None, lookup.provider
        suggestion = lookup.suggestions[0]
        if not suggestion.airport_codes or len(suggestion.airport_codes) != 1:
            return None, None, lookup.provider
        label = suggestion.city_name or suggestion.display_name
        return suggestion.airport_codes[0], label, lookup.provider

    @staticmethod
    def _destination_from_catalog(
        idea: DestinationIdea,
        reference,
        *,
        catalog: AirportCatalog,
    ) -> _ResolvedDestination | None:
        if reference.kind not in {LocationKind.CITY, LocationKind.AIRPORT}:
            return None
        codes = tuple(dict.fromkeys(reference.airport_candidates))
        if not codes or not reference.country_code:
            return None
        try:
            airport = catalog.get(codes[0])
        except ValueError:
            return None
        city = reference.normalized_name
        if reference.kind is LocationKind.AIRPORT:
            city = airport.city_name_en
        city = " ".join(city.strip().split())
        if not city:
            return None
        return _ResolvedDestination(
            idea=idea,
            city=city,
            country_code=reference.country_code,
            airport_codes=codes,
        )

    @staticmethod
    def _dynamic_suggestion(
        idea: DestinationIdea,
        suggestion,
    ) -> tuple[int, _ResolvedDestination, tuple[str, ...]] | None:
        if suggestion.kind is LocationSuggestionKind.CITY:
            codes = tuple(dict.fromkeys(suggestion.airport_codes))
        else:
            if suggestion.iata_code is None or suggestion.iata_code not in suggestion.airport_codes:
                return None
            codes = (suggestion.iata_code,)
        if not codes:
            return None
        city = " ".join((suggestion.city_name or suggestion.display_name).strip().split())
        if not city:
            return None
        query = normalize_vietnamese_alias(idea.place_query)
        names = {
            normalize_vietnamese_alias(value)
            for value in (suggestion.city_name, suggestion.display_name)
            if isinstance(value, str) and value.strip()
        }
        display_head = normalize_vietnamese_alias(suggestion.display_name.split(",", 1)[0])
        score = 10
        if query in names or query == display_head:
            score = 100
        elif query in names or any(name in query for name in names if name):
            score = 60
        return (
            score,
            _ResolvedDestination(
                idea=idea,
                city=city,
                country_code=suggestion.country_code,
                airport_codes=codes,
            ),
            codes,
        )

    async def _scope_country_code(
        self,
        scope: str,
        *,
        locale: Literal["vi", "en"],
        trace_id: str,
    ) -> str | None:
        try:
            reference = self.catalog.resolve_location(scope)
        except (TypeError, ValueError):
            reference = None
        if reference is not None and reference.country_code is not None:
            return reference.country_code
        if self.location_resolution is None:
            return None
        try:
            lookup = await self.location_resolution.resolve(
                LocationLookupRequest(query=scope, locale=locale, limit=8),
                correlation_id=trace_id,
            )
        except ProviderError:
            return None
        country_codes = {
            suggestion.country_code for suggestion in lookup.suggestions if suggestion.country_code
        }
        if len(country_codes) != 1:
            return None
        return next(iter(country_codes))

    async def _resolve_ideas(
        self,
        ideas: tuple[DestinationIdea, ...],
        *,
        locale: Literal["vi", "en"],
        trace_id: str,
    ) -> _IdeaResolution:
        resolved: list[_ResolvedDestination] = []
        groups: set[tuple[str, ...]] = set()
        rejected: list[str] = []
        location_failures = False
        had_empty = False
        for idea in ideas[: self.settings.max_candidates]:
            try:
                catalog_reference = self.catalog.resolve_location(idea.place_query)
            except (TypeError, ValueError):
                catalog_reference = None
            if catalog_reference is not None and catalog_reference.kind is not LocationKind.UNKNOWN:
                destination = self._destination_from_catalog(
                    idea,
                    catalog_reference,
                    catalog=self.catalog,
                )
                if destination is None:
                    rejected.append(idea.place_query)
                    continue
                if destination.airport_codes in groups:
                    continue
                groups.add(destination.airport_codes)
                resolved.append(destination)
                continue
            if self.location_resolution is None:
                location_failures = True
                rejected.append(idea.place_query)
                continue
            try:
                lookup = await self.location_resolution.resolve(
                    LocationLookupRequest(
                        query=idea.place_query,
                        locale=locale,
                        limit=8,
                    ),
                    correlation_id=trace_id,
                )
            except ProviderError:
                location_failures = True
                continue
            if not lookup.suggestions:
                had_empty = True
                rejected.append(idea.place_query)
                continue
            matches = [
                match
                for suggestion in lookup.suggestions
                if (match := self._dynamic_suggestion(idea, suggestion)) is not None
            ]
            if not matches:
                rejected.append(idea.place_query)
                continue
            best_score = max(match[0] for match in matches)
            best = [match for match in matches if match[0] == best_score]
            unique_best = {match[2]: match for match in best}
            if len(unique_best) != 1:
                # Never select the first provider suggestion when the query remains ambiguous.
                rejected.append(idea.place_query)
                continue
            destination = next(iter(unique_best.values()))[1]
            if destination.airport_codes in groups:
                continue
            groups.add(destination.airport_codes)
            resolved.append(destination)
        return _IdeaResolution(
            destinations=tuple(resolved),
            provider_failures=location_failures,
            had_empty=had_empty,
            rejected_places=tuple(dict.fromkeys(rejected))[:8],
        )

    @staticmethod
    def _diverse_destinations(
        destinations: tuple[_ResolvedDestination, ...],
        *,
        explicit_scope: str | None,
    ) -> tuple[_ResolvedDestination, ...]:
        if explicit_scope is not None:
            return destinations
        by_country: dict[str, list[_ResolvedDestination]] = {}
        for destination in destinations:
            by_country.setdefault(destination.country_code, []).append(destination)
        if len(by_country) <= 1:
            return destinations
        ordered: list[_ResolvedDestination] = []
        for index in range(max(len(items) for items in by_country.values())):
            for items in by_country.values():
                if index < len(items):
                    ordered.append(items[index])
        return tuple(ordered)

    @staticmethod
    def _sample_dates(date_window: TravelDateWindow) -> tuple[date, ...]:
        dates = tuple(
            date_window.start_date + timedelta(days=index)
            for index in range((date_window.end_date - date_window.start_date).days + 1)
        )
        if len(dates) <= 3:
            return dates
        sampled = [dates[0], dates[len(dates) // 2], dates[-1]]
        sampled.extend(day for day in dates if day not in sampled)
        return tuple(sampled)

    def _jobs(
        self,
        destinations: tuple[_ResolvedDestination, ...],
        *,
        date_window: TravelDateWindow,
    ) -> tuple[_SearchJob, ...]:
        if not destinations:
            return ()
        sampled_dates = self._sample_dates(date_window)
        jobs: list[_SearchJob] = []
        seen: set[tuple[str, date]] = set()

        def add(destination: _ResolvedDestination, airport_code: str, departure_date: date) -> None:
            if len(jobs) >= self.settings.max_flight_calls:
                return
            key = (airport_code, departure_date)
            if key in seen:
                return
            seen.add(key)
            jobs.append(_SearchJob(destination, airport_code, departure_date))

        # Every destination receives one primary-airport search before a second
        # date or secondary airport is considered.
        for departure_date in sampled_dates:
            for destination in destinations:
                add(destination, destination.airport_codes[0], departure_date)
                if len(jobs) >= self.settings.max_flight_calls:
                    return tuple(jobs)

        # If budget remains, inspect secondary airports in the same fair
        # destination/date order.
        max_airports = max(len(destination.airport_codes) for destination in destinations)
        for airport_index in range(1, max_airports):
            for departure_date in sampled_dates:
                for destination in destinations:
                    if airport_index < len(destination.airport_codes):
                        add(
                            destination,
                            destination.airport_codes[airport_index],
                            departure_date,
                        )
                        if len(jobs) >= self.settings.max_flight_calls:
                            return tuple(jobs)
        return tuple(jobs)

    async def _run_search(
        self,
        user_id: UUID,
        job: _SearchJob,
        *,
        return_date: date | None,
        passengers,
        cabin,
        currency: CurrencyCode,
        trace_id: str,
        semaphore: asyncio.Semaphore,
        origin: str,
        max_stops: int | None = None,
        baggage_required: bool | None = None,
        preferred_departure_start: time | None = None,
        preferred_departure_end: time | None = None,
    ) -> _SearchOutcome:
        criteria = FlightSearchCriteria(
            origin=origin,
            destination=job.airport_code,
            departure_date=job.departure_date,
            return_date=return_date,
            passengers=passengers,
            cabin=cabin,
            currency=currency,
            max_stops=max_stops,
            baggage_required=baggage_required,
            preferred_departure_start=preferred_departure_start,
            preferred_departure_end=preferred_departure_end,
        )
        try:
            async with semaphore:
                result = await self.flight_search_application.search_exact(
                    user_id,
                    criteria,
                    trace_id=trace_id,
                )
            return _SearchOutcome(job=job, result=result)
        except ProviderError:
            return _SearchOutcome(job=job, result=None, provider_failed=True)
        except Exception:
            logger.warning("trip inspiration search failed", extra={"trace_id": trace_id})
            return _SearchOutcome(job=job, result=None, provider_failed=True)

    async def inspire(
        self,
        user_id: UUID,
        *,
        message: str,
        locale: Literal["vi", "en"],
        safe_context: Mapping[str, object] | None = None,
        trusted_preferences: Mapping[str, object] | None = None,
        trace_id: str | None = None,
        destination_scope: str | None = None,
        request_alternatives: bool = False,
        semantic_updates: Mapping[str, object] | None = None,
    ) -> tuple[TripInspirationResult, TripInspirationCheckpoint]:
        trace = _trace_id(trace_id)
        prior = self._prior_checkpoint(safe_context)
        prior_origin = prior.origin if prior else None
        prior_dates = prior.date_window if prior else None
        prior_return = prior.return_date if prior else None
        prior_budget = prior.airfare_budget if prior else None
        prior_total_trip_budget = prior.total_trip_budget if prior else None
        prior_scope = prior.budget_scope if prior else BudgetScope.UNKNOWN
        prior_budget_allocation = prior.budget_allocation if prior else "unknown"
        prior_optimization = prior.optimization if prior else None
        prior_interests = prior.interests if prior else ()
        active_destination_scope = destination_scope or (prior.destination_scope if prior else None)
        excluded_destinations = list(prior.excluded_destinations if prior else ())
        if request_alternatives and prior is not None:
            excluded_destinations.extend(option.city for option in prior.options)
        excluded_destinations = list(dict.fromkeys(excluded_destinations))[:20]
        prior_pending = prior.pending_clarification if prior else None
        prior_pending_amount = prior.pending_budget_amount if prior else None
        passengers = prior.passengers if prior else PassengerMix()
        cabin = prior.cabin if prior else CabinClass.ECONOMY
        pending_clarification = prior_pending
        pending_budget_amount = prior_pending_amount
        validated_semantics = ValidatedSemanticUpdates()
        if semantic_updates:
            validated_semantics = apply_semantic_updates(
                current_message=message,
                plan=semantic_updates,
                safe_context=safe_context,
                clock=self.clock,
                timezone=self.date_resolution.timezone,
            )
        destination_update = validated_semantics.destination
        destination_clear = (
            destination_update is not None and destination_update.operation == "clear"
        )
        if destination_clear:
            active_destination_scope = None
        elif destination_update is not None and destination_update.operation != "none":
            if destination_update.mode in {"anywhere_within_scope", "domestic_only"}:
                active_destination_scope = destination_update.scope_query
            elif destination_update.mode == "specific":
                active_destination_scope = destination_update.place_query

        optimization = prior_optimization
        if validated_semantics.search is not None:
            refinement = validated_semantics.search
            if refinement.operation == "clear":
                optimization = None
            elif refinement.operation != "none":
                parsed_optimization = _canonical_optimization(refinement)
                if parsed_optimization is not None:
                    optimization = parsed_optimization

        parsed_passengers, party_mentioned, party_error = _extract_passengers(message, passengers)
        if party_mentioned and party_error is None:
            passengers = parsed_passengers
        if (
            validated_semantics.passengers is not None
            and validated_semantics.passengers.operation not in {"none", "clear"}
        ):
            semantic_passengers = validated_semantics.passengers
            replacement_default = 0 if semantic_passengers.operation == "replace" else None
            passenger_values = {
                "adults": semantic_passengers.adults
                if semantic_passengers.adults is not None
                else passengers.adults,
                "children": semantic_passengers.children
                if semantic_passengers.children is not None
                else passengers.children
                if replacement_default is None
                else replacement_default,
                "infants": semantic_passengers.infants
                if semantic_passengers.infants is not None
                else passengers.infants
                if replacement_default is None
                else replacement_default,
            }
            passengers = PassengerMix(**passenger_values)
            party_mentioned = True
            party_error = None
        parsed_cabin = _extract_cabin(message)
        if parsed_cabin is not None:
            cabin = parsed_cabin
        if validated_semantics.search is not None and validated_semantics.search.cabin is not None:
            cabin = CabinClass(validated_semantics.search.cabin)
        semantic_interests = (
            validated_semantics.destination.interests
            if validated_semantics.destination is not None
            else ()
        )
        if semantic_interests:
            interests = tuple(dict.fromkeys((*prior_interests, *semantic_interests)))[:5]
        else:
            interests = tuple(dict.fromkeys((*prior_interests, *_extract_interests(message))))[:5]
        if validated_semantics.destination is not None:
            excluded_destinations.extend(validated_semantics.destination.excluded_place_queries)
            excluded_destinations = list(dict.fromkeys(excluded_destinations))[:20]

        budget_amount, message_currency, budget_mentioned = _extract_budget(message)
        message_scope = _extract_budget_scope(message)
        semantic_budget = validated_semantics.budget
        budget_clear = semantic_budget is not None and semantic_budget.operation == "clear"
        budget_allocation = prior_budget_allocation
        if semantic_budget is not None and semantic_budget.allocation != "unknown":
            budget_allocation = semantic_budget.allocation
        if semantic_budget is not None and semantic_budget.operation not in {"none", "clear"}:
            if semantic_budget.amount_text:
                semantic_amount, semantic_amount_mentioned = _extract_budget_amount(
                    semantic_budget.amount_text,
                    allow_bare=True,
                )
                if semantic_amount is not None:
                    # Resolve the bounded span selected by DeepSeek. A second
                    # whole-message regex pass must not win a disagreement.
                    budget_amount = semantic_amount
                    budget_mentioned = semantic_amount_mentioned
                else:
                    budget_amount, budget_mentioned = _extract_budget_amount(
                        message,
                        allow_bare=True,
                    )
            if semantic_budget.currency_hint:
                try:
                    message_currency = semantic_budget.currency_hint.upper()
                except AttributeError:
                    message_currency = None
            if semantic_budget.scope == "airfare_only":
                message_scope = BudgetScope.AIRFARE_ONLY
            elif semantic_budget.scope == "total_trip":
                message_scope = BudgetScope.TOTAL_TRIP
        if budget_clear:
            budget_amount = None
            message_currency = None
            budget_mentioned = False
            message_scope = BudgetScope.UNKNOWN
            budget_allocation = "unknown"
        if (
            pending_clarification is TripInspirationPendingClarification.AIRFARE_ALLOCATION
            and not budget_mentioned
        ):
            budget_amount, budget_mentioned = _extract_budget_amount(message, allow_bare=True)
        budget_parse_error = budget_mentioned and budget_amount is None
        airfare_budget = None if budget_clear else prior_budget
        total_trip_budget = None if budget_clear else prior_total_trip_budget
        budget_scope = BudgetScope.UNKNOWN if budget_clear else prior_scope
        if budget_clear:
            if pending_clarification in {
                TripInspirationPendingClarification.BUDGET_CURRENCY,
                TripInspirationPendingClarification.BUDGET_SCOPE,
                TripInspirationPendingClarification.AIRFARE_ALLOCATION,
                TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY,
            }:
                pending_clarification = None
            pending_budget_amount = None
        use_all_total_for_airfare = (
            not budget_clear
            and prior_pending is TripInspirationPendingClarification.AIRFARE_ALLOCATION
            and total_trip_budget is not None
            and _uses_all_total_budget_for_airfare(message)
        )

        if (
            semantic_budget is not None
            and semantic_budget.mode == "increase_by"
            and budget_amount is not None
            and prior_budget is not None
        ):
            if message_currency is None:
                message_currency = prior_budget.currency
            if message_currency == prior_budget.currency:
                budget_amount += prior_budget.amount

        if budget_mentioned and budget_amount is not None:
            if message_currency is None:
                airfare_budget = None
                pending_budget_amount = budget_amount
                pending_clarification = (
                    TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY
                    if pending_clarification
                    in {
                        TripInspirationPendingClarification.AIRFARE_ALLOCATION,
                        TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY,
                    }
                    else TripInspirationPendingClarification.BUDGET_CURRENCY
                )
            else:
                airfare_budget = Money(amount=budget_amount, currency=message_currency)
                pending_budget_amount = None
                if pending_clarification in {
                    TripInspirationPendingClarification.BUDGET_CURRENCY,
                    TripInspirationPendingClarification.AIRFARE_ALLOCATION,
                    TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY,
                }:
                    if pending_clarification in {
                        TripInspirationPendingClarification.AIRFARE_ALLOCATION,
                        TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY,
                    }:
                        budget_scope = BudgetScope.AIRFARE_ONLY
                    pending_clarification = None
        elif (
            message_currency is not None
            and pending_clarification
            in {
                TripInspirationPendingClarification.BUDGET_CURRENCY,
                TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY,
            }
            and pending_budget_amount is not None
        ):
            airfare_budget = Money(
                amount=pending_budget_amount,
                currency=message_currency,
            )
            if (
                pending_clarification
                is TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY
            ):
                budget_scope = BudgetScope.AIRFARE_ONLY
            pending_budget_amount = None
            pending_clarification = None

        if use_all_total_for_airfare:
            airfare_budget = total_trip_budget
            total_trip_budget = None
            budget_scope = BudgetScope.AIRFARE_ONLY
            pending_budget_amount = None
            pending_clarification = None
        elif message_scope is not BudgetScope.UNKNOWN:
            budget_scope = message_scope
            if budget_scope is BudgetScope.TOTAL_TRIP and airfare_budget is not None:
                total_trip_budget = airfare_budget
            if pending_clarification is TripInspirationPendingClarification.BUDGET_SCOPE:
                pending_clarification = None
        elif airfare_budget is not None and budget_scope is BudgetScope.UNKNOWN:
            # An unqualified amount is an airfare budget. This keeps the
            # conversational path moving without asking for a scope choice.
            budget_scope = BudgetScope.AIRFARE_ONLY
            if pending_clarification is TripInspirationPendingClarification.BUDGET_SCOPE:
                pending_clarification = None

        if (
            airfare_budget is not None
            and budget_scope is BudgetScope.AIRFARE_ONLY
            and pending_clarification
            not in {
                TripInspirationPendingClarification.AIRFARE_ALLOCATION,
                TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY,
            }
        ):
            total_trip_budget = None

        if party_error is not None:
            # Passenger validation has priority over any other clarification. A
            # pending monetary amount cannot coexist with this single pending
            # field, so it is discarded rather than being misrepresented.
            pending_clarification = TripInspirationPendingClarification.PASSENGERS
            pending_budget_amount = None
        elif prior_pending is TripInspirationPendingClarification.PASSENGERS:
            if party_mentioned:
                if pending_clarification is TripInspirationPendingClarification.PASSENGERS:
                    pending_clarification = None
            else:
                pending_clarification = TripInspirationPendingClarification.PASSENGERS
                pending_budget_amount = None

        if (
            budget_parse_error
            and pending_clarification is not TripInspirationPendingClarification.PASSENGERS
        ):
            pending_clarification = TripInspirationPendingClarification.AIRFARE_ALLOCATION
            pending_budget_amount = None

        try:
            resolved_dates = self.date_resolution.resolve(message, locale=locale)
        except DateResolutionError:
            resolved_dates = None
        date_window = validated_semantics.temporal_window or resolved_dates or prior_dates
        return_date = _extract_return_date(message) or prior_return
        if (
            date_window is not None
            and pending_clarification is TripInspirationPendingClarification.DATE_WINDOW
        ):
            pending_clarification = None
        origin = prior_origin
        origin_label = None

        def make_checkpoint(
            *,
            options: tuple[TripInspirationPresentedOption, ...] = (),
        ) -> TripInspirationCheckpoint:
            return self._make_checkpoint(
                origin=origin,
                date_window=date_window,
                return_date=return_date,
                airfare_budget=airfare_budget,
                total_trip_budget=total_trip_budget,
                budget_scope=budget_scope,
                interests=interests,
                budget_allocation=budget_allocation,
                optimization=optimization,
                destination_scope=active_destination_scope,
                excluded_destinations=tuple(excluded_destinations),
                options=options,
                passengers=passengers,
                cabin=cabin,
                pending_clarification=pending_clarification,
                pending_budget_amount=pending_budget_amount,
            )

        if (
            party_error is not None
            or pending_clarification is TripInspirationPendingClarification.PASSENGERS
        ):
            if party_error is not None and origin is None:
                explicit_origin = _extract_origin_query(message)
                if explicit_origin is not None:
                    try:
                        reference = self.catalog.resolve_location(explicit_origin)
                    except (TypeError, ValueError):
                        reference = None
                    if (
                        reference is not None
                        and reference.kind in {LocationKind.CITY, LocationKind.AIRPORT}
                        and len(reference.airport_candidates) == 1
                    ):
                        origin = reference.airport_candidates[0]
            checkpoint = make_checkpoint()
            constraints = self._result_constraints(
                origin=origin,
                date_window=date_window,
                return_date=return_date,
                airfare_budget=airfare_budget,
                budget_scope=budget_scope,
                passengers=passengers,
                cabin=cabin,
                interests=interests,
                budget_allocation=budget_allocation,
                optimization=optimization,
            )
            return self._clarification(
                locale=locale,
                trace_id=trace,
                missing_fields=("passengers",),
                question_vi="Số lượng người lớn, trẻ em và em bé chưa hợp lệ. Vui lòng cho biết số lượng chính xác.",
                question_en="The passenger mix is not valid. Please provide the number of adults, children, and infants.",
                constraints=constraints,
                checkpoint=checkpoint,
            )

        if pending_clarification is TripInspirationPendingClarification.AIRFARE_ALLOCATION and not (
            budget_mentioned and budget_amount is not None
        ):
            checkpoint = make_checkpoint()
            constraints = self._result_constraints(
                origin=origin,
                date_window=date_window,
                return_date=return_date,
                airfare_budget=airfare_budget,
                budget_scope=budget_scope,
                passengers=passengers,
                cabin=cabin,
                interests=interests,
                budget_allocation=budget_allocation,
                optimization=optimization,
            )
            return self._clarification(
                locale=locale,
                trace_id=trace,
                missing_fields=("airfare_budget",),
                question_vi="Bạn muốn dành bao nhiêu cho vé máy bay?",
                question_en="What budget should I use for airfare?",
                constraints=constraints,
                checkpoint=checkpoint,
            )

        origin_query = None
        if (
            validated_semantics.origin is not None
            and validated_semantics.origin.mode == "specific"
            and validated_semantics.origin.place_query
        ):
            origin_query = validated_semantics.origin.place_query
        if origin_query is None:
            origin_query = _extract_origin_query(message)
        if origin_query is None and prior_pending is TripInspirationPendingClarification.ORIGIN:
            origin_query = _extract_bare_origin_query(message)
        if origin_query is not None:
            try:
                origin, origin_label, _ = await self._resolve_origin(
                    origin_query,
                    locale=locale,
                    trace_id=trace,
                )
            except ProviderError:
                pending_clarification = TripInspirationPendingClarification.ORIGIN
                constraints = self._result_constraints(
                    origin=None,
                    date_window=date_window,
                    return_date=return_date,
                    airfare_budget=airfare_budget,
                    budget_scope=budget_scope,
                    passengers=passengers,
                    cabin=cabin,
                    interests=interests,
                    budget_allocation=budget_allocation,
                    optimization=optimization,
                )
                return (
                    TripInspirationResult(
                        status=TripInspirationStatus.PROVIDER_UNAVAILABLE,
                        constraints=constraints,
                        limitations=("Location resolution is temporarily unavailable.",),
                        safe_error_code="location_provider_unavailable",
                        retryable=True,
                        trace_id=trace,
                    ),
                    make_checkpoint(),
                )
            if origin is None:
                pending_clarification = TripInspirationPendingClarification.ORIGIN
                constraints = self._result_constraints(
                    origin=None,
                    date_window=date_window,
                    return_date=return_date,
                    airfare_budget=airfare_budget,
                    budget_scope=budget_scope,
                    passengers=passengers,
                    cabin=cabin,
                    interests=interests,
                    budget_allocation=budget_allocation,
                    optimization=optimization,
                )
                return self._clarification(
                    locale=locale,
                    trace_id=trace,
                    missing_fields=("origin",),
                    question_vi="Tôi không tìm thấy sân bay khởi hành cụ thể. Bạn sẽ khởi hành từ thành phố hoặc sân bay nào?",
                    question_en="I could not find one specific departure airport. Which city or airport will you depart from?",
                    constraints=constraints,
                    checkpoint=make_checkpoint(),
                )
            if pending_clarification is TripInspirationPendingClarification.ORIGIN:
                pending_clarification = None
        elif origin is None and isinstance(trusted_preferences, Mapping):
            default_origin = trusted_preferences.get("default_origin_airport")
            if isinstance(default_origin, str):
                reference = self.catalog.resolve_location(default_origin)
                if reference.kind is LocationKind.AIRPORT and reference.airport_candidates:
                    origin = reference.airport_candidates[0]
                    origin_label = reference.normalized_name
        elif origin is not None:
            origin_label = self.catalog.get(origin).city_name_en

        constraints = self._result_constraints(
            origin=origin,
            date_window=date_window,
            return_date=return_date,
            airfare_budget=airfare_budget,
            budget_scope=budget_scope,
            budget_allocation=budget_allocation,
            optimization=optimization,
            passengers=passengers,
            cabin=cabin,
            interests=interests,
        )
        if pending_clarification in {
            TripInspirationPendingClarification.BUDGET_CURRENCY,
            TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY,
        }:
            missing = (
                "airfare_budget_currency"
                if pending_clarification
                is TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY
                else "currency"
            )
            question_vi = "Ngân sách này dùng loại tiền nào, ví dụ USD hoặc VND?"
            question_en = "What currency should I use for that budget, such as USD or VND?"
            return self._clarification(
                locale=locale,
                trace_id=trace,
                missing_fields=(missing,),
                question_vi=question_vi,
                question_en=question_en,
                constraints=constraints,
                checkpoint=make_checkpoint(),
            )

        if origin is None:
            pending_clarification = TripInspirationPendingClarification.ORIGIN
            checkpoint = make_checkpoint()
            constraints = self._result_constraints(
                origin=None,
                date_window=date_window,
                return_date=return_date,
                airfare_budget=airfare_budget,
                budget_scope=budget_scope,
                passengers=passengers,
                cabin=cabin,
                interests=interests,
                budget_allocation=budget_allocation,
                optimization=optimization,
            )
            return self._clarification(
                locale=locale,
                trace_id=trace,
                missing_fields=("origin",),
                question_vi="Bạn sẽ khởi hành từ thành phố hoặc sân bay nào?",
                question_en="Which city or airport will you depart from?",
                constraints=constraints,
                checkpoint=checkpoint,
            )

        checkpoint = make_checkpoint()
        if date_window is None:
            pending_clarification = TripInspirationPendingClarification.DATE_WINDOW
            checkpoint = make_checkpoint()
            return self._clarification(
                locale=locale,
                trace_id=trace,
                missing_fields=("date_window",),
                question_vi="Bạn muốn đi vào ngày nào hoặc trong khoảng ngày nào?",
                question_en="What travel date or date window would you like?",
                constraints=constraints,
                checkpoint=checkpoint,
            )
        needs_fare_budget_for_optimization = bool(
            optimization is not None
            and optimization.metric == "fare"
            and optimization.direction == "maximize"
        )
        if airfare_budget is None and needs_fare_budget_for_optimization:
            pending_clarification = TripInspirationPendingClarification.AIRFARE_ALLOCATION
            checkpoint = make_checkpoint()
            return self._clarification(
                locale=locale,
                trace_id=trace,
                missing_fields=("airfare_budget",),
                question_vi="Bạn muốn dùng ngân sách vé máy bay tối đa bao nhiêu để tôi tìm lựa chọn đắt nhất nhưng không vượt quá mức đó?",
                question_en="What is your maximum airfare budget so I can find the most expensive option without exceeding it?",
                constraints=constraints,
                checkpoint=checkpoint,
            )
        if airfare_budget is None and budget_scope is BudgetScope.AIRFARE_ONLY:
            return self._clarification(
                locale=locale,
                trace_id=trace,
                missing_fields=("airfare_budget",),
                question_vi="Bạn muốn dành bao nhiêu cho vé máy bay?",
                question_en="What budget should I use for airfare?",
                constraints=constraints,
                checkpoint=checkpoint,
            )
        if return_date is not None and return_date < date_window.start_date:
            return self._clarification(
                locale=locale,
                trace_id=trace,
                missing_fields=("return_date",),
                question_vi="Vui lòng cho biết ngày về sau ngày đi.",
                question_en="Please provide a return date after the departure date.",
                constraints=constraints,
                checkpoint=checkpoint,
            )
        if budget_scope is BudgetScope.TOTAL_TRIP:
            # Keep the original amount in a dedicated typed field so a natural
            # follow-up such as "use all of it for flights" can reuse it safely.
            total_trip_budget = airfare_budget or total_trip_budget
            airfare_budget = None
            pending_clarification = TripInspirationPendingClarification.AIRFARE_ALLOCATION
            checkpoint = make_checkpoint()
            return self._clarification(
                locale=locale,
                trace_id=trace,
                missing_fields=("airfare_budget",),
                question_vi="Hiện tôi chỉ có thể xác minh giá vé máy bay, chưa xác minh được chi phí khách sạn, ăn uống và hoạt động. Bạn muốn dành bao nhiêu cho vé máy bay?",
                question_en="I can currently verify airfare, not the complete hotel, food, and activity cost. What portion should I reserve for flights?",
                constraints=constraints,
                checkpoint=checkpoint,
                message_vi="Ngân sách toàn chuyến cần được chuyển thành ngân sách vé máy bay trước khi tôi so sánh.",
                message_en="A total-trip budget must be converted into an airfare budget before I can compare verified offers.",
            )

        currency: CurrencyCode = airfare_budget.currency if airfare_budget else "VND"
        comparison_airfare_budget = airfare_budget
        if airfare_budget is not None and budget_allocation == "per_person":
            comparison_airfare_budget = Money(
                amount=airfare_budget.amount * passengers.total,
                currency=airfare_budget.currency,
            )
        try:
            origin_airport = self.catalog.get(origin)
            origin_label = origin_label or origin_airport.city_name_en
        except ValueError:
            origin_label = origin_label or origin
        try:
            origin_airport = self.catalog.get(origin)
            if origin_label is None:
                origin_label = (
                    origin_airport.city_name_vi if locale == "vi" else origin_airport.city_name_en
                )
        except ValueError:
            origin_label = origin_label or origin
        candidate_limit = self.settings.max_candidates
        candidate_request = TripInspirationCandidateRequest(
            origin_airport=origin,
            origin_label=origin_label,
            date_window=date_window,
            return_date=return_date,
            airfare_budget=airfare_budget,
            budget_scope=budget_scope,
            passengers=passengers,
            cabin=cabin,
            interests=interests,
            budget_allocation=budget_allocation,
            optimization=optimization,
            destination_scope=active_destination_scope,
            excluded_places=tuple(excluded_destinations),
            locale=locale,
            maximum_candidates=candidate_limit,
        )
        try:
            candidate_result = await self.llm.suggest_trip_destinations(candidate_request)
        except (LLMUnavailableError, LLMOutputError, AttributeError) as exc:
            safe_error_code = getattr(exc, "safe_code", "llm_contract_unavailable")
            logger.warning(
                "trip inspiration candidate generation failed",
                extra={"error_code": safe_error_code, "trace_id": trace},
            )
            return (
                TripInspirationResult(
                    status=TripInspirationStatus.PROVIDER_UNAVAILABLE,
                    constraints=constraints,
                    message_vi="Dịch vụ gợi ý điểm đến hiện tạm thời không khả dụng. Không có điểm đến chưa xác minh nào được hiển thị.",
                    message_en="The destination idea service is temporarily unavailable. No unverified destination was shown.",
                    limitations=("You can retry when the service is available again.",),
                    safe_error_code=safe_error_code,
                    retryable=True,
                    trace_id=trace,
                ),
                checkpoint,
            )
        ideas = tuple(candidate_result.ideas[:candidate_limit])
        if not ideas:
            return self._no_results(
                reason=TripInspirationNoResultReason.CANDIDATE_GENERATION_EMPTY,
                constraints=constraints,
                trace_id=trace,
                origin_label=origin_label,
                airfare_budget=airfare_budget,
                checkpoint=checkpoint,
            )

        resolution = await self._resolve_ideas(
            ideas,
            locale=locale,
            trace_id=trace,
        )
        repair_attempted = False
        if not resolution.destinations and not (
            resolution.provider_failures and not resolution.had_empty
        ):
            repair_attempted = True
            repair_request = candidate_request.model_copy(
                update={"rejected_places": resolution.rejected_places}
            )
            try:
                repair_result = await self.llm.suggest_trip_destinations(repair_request)
            except (LLMUnavailableError, LLMOutputError, AttributeError) as exc:
                safe_error_code = getattr(exc, "safe_code", "llm_contract_unavailable")
                logger.warning(
                    "trip inspiration candidate repair failed",
                    extra={"error_code": safe_error_code, "trace_id": trace},
                )
                return (
                    TripInspirationResult(
                        status=TripInspirationStatus.PROVIDER_UNAVAILABLE,
                        constraints=constraints,
                        message_vi="Dịch vụ gợi ý điểm đến hiện tạm thời không khả dụng. Không có điểm đến chưa xác minh nào được hiển thị.",
                        message_en="The destination idea service is temporarily unavailable. No unverified destination was shown.",
                        limitations=("You can retry when the service is available again.",),
                        safe_error_code=safe_error_code,
                        retryable=True,
                        trace_id=trace,
                    ),
                    checkpoint,
                )
            repair_ideas = tuple(repair_result.ideas[:candidate_limit])
            if repair_ideas:
                existing_queries = {idea.place_query.casefold() for idea in ideas}
                ideas = (
                    *ideas,
                    *(
                        idea
                        for idea in repair_ideas
                        if idea.place_query.casefold() not in existing_queries
                    ),
                )
                resolution = await self._resolve_ideas(
                    repair_ideas,
                    locale=locale,
                    trace_id=trace,
                )

        if not resolution.destinations:
            if resolution.provider_failures and not resolution.had_empty:
                return (
                    TripInspirationResult(
                        status=TripInspirationStatus.PROVIDER_UNAVAILABLE,
                        constraints=constraints,
                        message_vi="Dịch vụ xác minh địa điểm hiện tạm thời không khả dụng. Không có điểm đến chưa xác minh nào được hiển thị.",
                        message_en="The location validation service is temporarily unavailable. No unverified destination was shown.",
                        limitations=("You can retry when the service is available again.",),
                        safe_error_code="location_provider_unavailable",
                        retryable=True,
                        trace_id=trace,
                    ),
                    checkpoint,
                )
            return self._no_results(
                reason=TripInspirationNoResultReason.CANDIDATE_VALIDATION_FAILED,
                constraints=constraints,
                trace_id=trace,
                origin_label=origin_label,
                airfare_budget=airfare_budget,
                limitations=("You can retry with a more specific city or airport name.",),
                checkpoint=checkpoint,
            )

        scoped_country_code = None
        scope_filter_required = bool(
            destination_update is not None
            and destination_update.operation not in {"none", "clear"}
            and destination_update.mode in {"specific", "anywhere_within_scope", "domestic_only"}
            and active_destination_scope
        )
        if scope_filter_required:
            scoped_country_code = await self._scope_country_code(
                active_destination_scope, locale=locale, trace_id=trace
            )
        excluded_keys = {value.casefold() for value in excluded_destinations}
        destinations = tuple(
            destination
            for destination in resolution.destinations
            if destination.city.casefold() not in excluded_keys
            and origin not in destination.airport_codes
            and (not scope_filter_required or scoped_country_code is not None)
            and (scoped_country_code is None or destination.country_code == scoped_country_code)
        )
        destination_semantic = validated_semantics.destination
        if destination_semantic is not None and destination_semantic.mode in {
            "international_only",
            "domestic_only",
        }:
            try:
                origin_country = self.catalog.get(origin).country_code
            except ValueError:
                origin_country = None
            if origin_country is not None:
                if destination_semantic.mode == "international_only":
                    destinations = tuple(
                        item for item in destinations if item.country_code != origin_country
                    )
                else:
                    destinations = tuple(
                        item for item in destinations if item.country_code == origin_country
                    )
        if not destinations:
            return self._no_results(
                reason=TripInspirationNoResultReason.CANDIDATE_GENERATION_EMPTY,
                constraints=constraints,
                trace_id=trace,
                origin_label=origin_label,
                airfare_budget=airfare_budget,
                checkpoint=checkpoint,
            )
        destinations = self._diverse_destinations(
            destinations,
            explicit_scope=active_destination_scope,
        )
        jobs = self._jobs(destinations, date_window=date_window)
        max_stops = None
        baggage_required = None
        preferred_departure_start = None
        preferred_departure_end = None
        ranking_optimization = optimization or optimization_preference(
            metric="fare",
            direction="minimize",
        )
        if validated_semantics.search is not None and validated_semantics.search.operation not in {
            "none",
            "clear",
        }:
            refinement = validated_semantics.search
            parsed_optimization = _canonical_optimization(refinement)
            if parsed_optimization is not None:
                ranking_optimization = parsed_optimization
            if refinement.direct_only is True:
                max_stops = 0
            if refinement.checked_baggage_required is not None:
                baggage_required = refinement.checked_baggage_required
            time_windows = {
                "morning": (time(5, 0), time(12, 0)),
                "afternoon": (time(12, 0), time(18, 0)),
                "evening": (time(18, 0), time(23, 59)),
                "night": (time(0, 0), time(5, 0)),
            }
            if refinement.time_of_day in time_windows:
                preferred_departure_start, preferred_departure_end = time_windows[
                    refinement.time_of_day
                ]
        if not jobs:
            return self._no_results(
                reason=TripInspirationNoResultReason.SEARCH_BUDGET_EXHAUSTED,
                constraints=constraints,
                trace_id=trace,
                origin_label=origin_label,
                airfare_budget=airfare_budget,
                checkpoint=checkpoint,
            )

        semaphore = asyncio.Semaphore(self.settings.concurrency)
        tasks = [
            asyncio.create_task(
                self._run_search(
                    user_id,
                    job,
                    return_date=return_date,
                    passengers=passengers,
                    cabin=cabin,
                    currency=currency,
                    trace_id=trace,
                    semaphore=semaphore,
                    origin=origin,
                    max_stops=max_stops,
                    baggage_required=baggage_required,
                    preferred_departure_start=preferred_departure_start,
                    preferred_departure_end=preferred_departure_end,
                )
            )
            for job in jobs
        ]
        done, pending = await asyncio.wait(tasks, timeout=self.settings.timeout_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        outcomes = [task.result() for task in done if not task.cancelled()]
        provider_failures = len(pending)
        successful_searches = 0
        offers_seen = 0
        same_currency_offers = 0
        offers_over_budget = 0
        offers_currency_mismatch = 0
        offers_converted = 0
        offers_fx_unavailable = 0
        offers_fx_invalid_or_expired = 0
        quote_cache: dict[tuple[str, str], ExchangeRateQuote] = {}
        quote_failure_cache: dict[tuple[str, str], str] = {}
        best: dict[tuple[str, ...], _RankedOffer] = {}

        async def quote_for(
            source_currency: str,
            target_currency: str,
        ) -> tuple[ExchangeRateQuote | None, str | None]:
            pair = (source_currency, target_currency)
            if pair in quote_cache:
                return quote_cache[pair], None
            if pair in quote_failure_cache:
                return None, quote_failure_cache[pair]
            if self.exchange_rates is None:
                quote_failure_cache[pair] = "unavailable"
                return None, "unavailable"
            try:
                quote = await self.exchange_rates.quote(
                    source_currency,
                    target_currency,
                    correlation_id=trace,
                )
                if not isinstance(quote, ExchangeRateQuote):
                    raise ExchangeRateError("exchange-rate provider returned an invalid quote")
                if (
                    quote.source_currency != source_currency
                    or quote.target_currency != target_currency
                    or not quote.rate.is_finite()
                    or quote.rate <= 0
                    or quote.expires_at <= self.clock.now()
                ):
                    raise ExchangeRateError("exchange-rate quote is invalid or expired")
            except ExchangeRateUnavailableError:
                quote_failure_cache[pair] = "unavailable"
                return None, "unavailable"
            except Exception:
                logger.warning(
                    "trip inspiration exchange-rate conversion failed",
                    extra={
                        "trace_id": trace,
                        "source_currency": source_currency,
                        "target_currency": target_currency,
                    },
                )
                quote_failure_cache[pair] = "invalid_or_expired"
                return None, "invalid_or_expired"
            quote_cache[pair] = quote
            return quote, None

        def price_order_key(candidate: _RankedOffer, *, maximize: bool = False) -> Decimal:
            amount = candidate.comparison_amount
            if amount is None:
                return Decimal("Infinity")
            return -amount if maximize else amount

        def offer_rank_key(candidate: _RankedOffer):
            offer = candidate.offer
            segments = offer.segments
            preference = ranking_optimization
            if preference.metric == "duration":
                duration = sum(
                    int((segment.arrival_at - segment.departure_at).total_seconds())
                    for segment in segments
                )
                primary = -duration if preference.direction == "maximize" else duration
            elif preference.metric == "stops":
                stops = max(len(segments) - 1, 0)
                primary = -stops if preference.direction == "maximize" else stops
            elif preference.metric == "departure_time":
                departure = segments[0].departure_at if segments else offer.expires_at
                primary = (
                    -departure.timestamp() if preference.direction == "maximize" else departure
                )
            else:
                primary = price_order_key(
                    candidate,
                    maximize=preference.direction == "maximize",
                )
            return (
                primary,
                price_order_key(candidate),
                offer.expires_at,
                str(offer.offer_id),
            )

        for outcome in outcomes:
            if outcome.provider_failed or outcome.result is None:
                provider_failures += 1
                continue
            result = outcome.result
            status_value = getattr(getattr(result, "status", None), "value", None)
            if status_value == "provider_unavailable":
                provider_failures += 1
                continue
            successful_searches += 1
            for offer in getattr(result, "offers", ()):
                if offer.destination != outcome.job.airport_code:
                    continue
                offers_seen += 1
                comparison_amount: Decimal | None = None
                budget_comparison: InspirationBudgetComparison | None = None
                if comparison_airfare_budget is not None:
                    if offer.currency == comparison_airfare_budget.currency:
                        same_currency_offers += 1
                        comparison_amount = offer.total
                        if offer.total > comparison_airfare_budget.amount:
                            offers_over_budget += 1
                            continue
                    else:
                        offers_currency_mismatch += 1
                        quote, failure = await quote_for(
                            comparison_airfare_budget.currency,
                            offer.currency,
                        )
                        if failure == "unavailable":
                            offers_fx_unavailable += 1
                            continue
                        if failure == "invalid_or_expired" or quote is None:
                            offers_fx_invalid_or_expired += 1
                            continue
                        offers_converted += 1
                        comparison_budget = Money(
                            amount=quantize_currency(
                                comparison_airfare_budget.amount * quote.rate,
                                offer.currency,
                            ),
                            currency=offer.currency,
                        )
                        approximate_fare = Money(
                            amount=quantize_currency(
                                offer.total / quote.rate,
                                comparison_airfare_budget.currency,
                            ),
                            currency=comparison_airfare_budget.currency,
                        )
                        budget_comparison = InspirationBudgetComparison(
                            user_budget=airfare_budget,
                            comparison_budget=comparison_budget,
                            approximate_fare=approximate_fare,
                            rate=quote.rate,
                            rate_source=quote.source,
                            rate_as_of=quote.as_of,
                            rate_expires_at=quote.expires_at,
                            is_demo_rate=quote.is_demo,
                        )
                        comparison_amount = approximate_fare.amount
                        if offer.total > comparison_budget.amount:
                            offers_over_budget += 1
                            continue
                else:
                    # A no-budget recommendation still defaults to cheapest-first. Compare
                    # provider fares in the requested search currency when possible; never
                    # sort raw amounts from different currencies as if they were equal.
                    if offer.currency == currency:
                        comparison_amount = offer.total
                    else:
                        quote, failure = await quote_for(offer.currency, currency)
                        if failure is None and quote is not None:
                            comparison_amount = quantize_currency(
                                offer.total * quote.rate,
                                currency,
                            )
                candidate = _RankedOffer(
                    destination=outcome.job.destination,
                    offer=offer,
                    comparison_amount=comparison_amount,
                    budget_comparison=budget_comparison,
                )
                key = outcome.job.destination.airport_codes
                existing = best.get(key)
                if existing is None or offer_rank_key(candidate) < offer_rank_key(existing):
                    best[key] = candidate
        _log_inspiration_metrics(
            trace,
            candidate_ideas_count=len(ideas),
            candidate_validated_count=len(destinations),
            candidate_rejected_count=len(resolution.rejected_places),
            repair_attempted=repair_attempted,
            country_count=len({destination.country_code for destination in destinations}),
            jobs_scheduled=len(jobs),
            call_budget=self.settings.max_flight_calls,
            offers_seen=offers_seen,
            offers_over_budget=offers_over_budget,
            offers_currency_mismatch=offers_currency_mismatch,
            offers_converted=offers_converted,
            offers_fx_unavailable=offers_fx_unavailable,
            offers_fx_invalid_or_expired=offers_fx_invalid_or_expired,
        )
        if not best:
            all_failed = provider_failures == len(jobs) and successful_searches == 0
            if all_failed:
                return (
                    TripInspirationResult(
                        status=TripInspirationStatus.PROVIDER_UNAVAILABLE,
                        constraints=constraints,
                        message_vi="Nhà cung cấp chuyến bay đang tạm thời không khả dụng. Không có điểm đến hoặc giá chưa xác minh nào được hiển thị.",
                        message_en="The flight provider is temporarily unavailable. No unverified destination or price was shown.",
                        limitations=("You can retry when the provider is available again.",),
                        safe_error_code="flight_provider_unavailable",
                        retryable=True,
                        trace_id=trace,
                    ),
                    checkpoint,
                )
            comparable_offers = same_currency_offers + offers_converted
            fx_failures = offers_fx_unavailable + offers_fx_invalid_or_expired
            if airfare_budget is not None and fx_failures > 0:
                reason = TripInspirationNoResultReason.CURRENCY_CONVERSION_UNAVAILABLE
            elif (
                airfare_budget is not None
                and comparable_offers > 0
                and offers_over_budget == comparable_offers
            ):
                reason = TripInspirationNoResultReason.OVER_BUDGET
            elif (
                airfare_budget is not None
                and comparable_offers == 0
                and offers_currency_mismatch > 0
            ):
                reason = TripInspirationNoResultReason.CURRENCY_CONVERSION_UNAVAILABLE
            else:
                reason = TripInspirationNoResultReason.NO_VERIFIED_OFFER
            return self._no_results(
                reason=reason,
                constraints=constraints,
                trace_id=trace,
                origin_label=origin_label,
                airfare_budget=airfare_budget,
                limitations=("Flight price and availability can change before booking.",),
                checkpoint=checkpoint,
            )

        def destination_rank_key(candidate: _RankedOffer):
            offer = candidate.offer
            segments = offer.segments
            preference = ranking_optimization
            if preference.metric == "duration":
                duration = sum(
                    int((segment.arrival_at - segment.departure_at).total_seconds())
                    for segment in segments
                )
                primary = -duration if preference.direction == "maximize" else duration
            elif preference.metric == "stops":
                stops = max(len(segments) - 1, 0)
                primary = -stops if preference.direction == "maximize" else stops
            elif preference.metric == "departure_time":
                departure = segments[0].departure_at if segments else offer.expires_at
                primary = (
                    -departure.timestamp() if preference.direction == "maximize" else departure
                )
            else:
                primary = price_order_key(
                    candidate,
                    maximize=preference.direction == "maximize",
                )
            return (
                primary,
                price_order_key(candidate),
                candidate.destination.city.casefold(),
                str(offer.offer_id),
            )

        ordered = sorted(best.values(), key=destination_rank_key)[: self.settings.result_limit]
        recommendations: list[TripInspirationRecommendation] = []
        for rank, candidate in enumerate(ordered, start=1):
            destination = candidate.destination
            offer = candidate.offer
            limitations = [
                "Flight price and availability can change before booking.",
                "The budget comparison covers airfare only.",
            ]
            if candidate.budget_comparison is not None:
                limitations.append(
                    "The budget comparison uses an approximate exchange rate; booking review uses the provider's current exact fare and currency."
                )
            recommendations.append(
                TripInspirationRecommendation(
                    rank=rank,
                    city=destination.city,
                    country_code=destination.country_code,
                    airport_codes=destination.airport_codes,
                    lowest_verified_fare=Money(amount=offer.total, currency=offer.currency),
                    retrieved_at=offer.retrieved_at,
                    expires_at=offer.expires_at,
                    application_offer_id=offer.offer_id,
                    search_id=next(
                        outcome.result.search_id
                        for outcome in outcomes
                        if outcome.result is not None
                        and any(item.offer_id == offer.offer_id for item in outcome.result.offers)
                    ),
                    reason=destination.idea.reason,
                    limitations=tuple(limitations),
                    budget_comparison=candidate.budget_comparison,
                )
            )
        options = tuple(
            TripInspirationPresentedOption(
                rank=item.rank,
                application_offer_id=item.application_offer_id,
                search_id=item.search_id,
                city=item.city,
                airport_codes=item.airport_codes,
                expires_at=item.expires_at,
            )
            for item in recommendations
        )
        new_checkpoint = self._make_checkpoint(
            origin=origin,
            date_window=date_window,
            return_date=return_date,
            airfare_budget=airfare_budget,
            total_trip_budget=total_trip_budget,
            budget_scope=budget_scope,
            interests=interests,
            budget_allocation=budget_allocation,
            optimization=optimization,
            destination_scope=active_destination_scope,
            excluded_destinations=tuple(excluded_destinations),
            options=options,
            passengers=passengers,
            cabin=cabin,
            pending_clarification=pending_clarification,
            pending_budget_amount=pending_budget_amount,
        )
        return (
            TripInspirationResult(
                status=TripInspirationStatus.RESULTS,
                constraints=constraints,
                recommendations=tuple(recommendations),
                message_vi=(
                    (
                        f"Tôi đã tìm thấy {len(recommendations)} điểm đến khác có giá vé hiện tại phù hợp."
                        if request_alternatives
                        else f"Tôi đã tìm thấy {len(recommendations)} điểm đến có giá vé hiện tại phù hợp."
                    )
                    + _optimization_note(ranking_optimization, locale="vi")
                ),
                message_en=(
                    (
                        f"I found {len(recommendations)} different destinations with current matching airfare."
                        if request_alternatives
                        else f"I found {len(recommendations)} destinations with current matching airfare."
                    )
                    + _optimization_note(ranking_optimization, locale="en")
                ),
                limitations=(
                    "Flight price and availability can change before booking.",
                    "The budget comparison covers airfare only.",
                ),
                trace_id=trace,
            ),
            new_checkpoint,
        )


__all__ = ["TripInspirationService", "TripInspirationSettings"]
