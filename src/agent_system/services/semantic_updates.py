from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_system.domain.flights import PassengerMix
from agent_system.domain.orchestration import (
    BudgetSemantic,
    DestinationSemantic,
    PassengerSemantic,
    PresentedResultReferenceSemantic,
    SearchRefinementSemantic,
    SemanticConstraintUpdates,
    TemporalSemantic,
)
from agent_system.domain.trip_discovery import TravelDateWindow
from agent_system.providers.clock import Clock
from agent_system.providers.localization import normalize_vietnamese_alias
from agent_system.services.date_resolution import (
    AmbiguousDateError,
    DateResolutionError,
    DateResolutionService,
    TripDiscoverySettings,
)


@dataclass
class SemanticPolicyRejectionError(ValueError):
    reason: str
    field: str
    question_vi: str
    question_en: str
    missing_fields: tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True)
class ValidatedSemanticUpdates:
    temporal_window: TravelDateWindow | None = None
    temporal: TemporalSemantic | None = None
    budget: BudgetSemantic | None = None
    passengers: PassengerSemantic | None = None
    destination: DestinationSemantic | None = None
    origin: DestinationSemantic | None = None
    search: SearchRefinementSemantic | None = None
    result_reference: PresentedResultReferenceSemantic | None = None
    referenced_rank: int | None = None
    changed_fields: tuple[str, ...] = ()

    @property
    def search_defining_change(self) -> bool:
        return bool(
            set(self.changed_fields).intersection(
                {"origin", "destination", "temporal", "budget", "passengers", "cabin"}
            )
        )


def _source_is_current_message(source_text: str, current_message: str) -> bool:
    return normalize_vietnamese_alias(source_text) in normalize_vietnamese_alias(current_message)


def _has_meaningful_result_reference(
    semantic: PresentedResultReferenceSemantic | None,
) -> bool:
    if semantic is None:
        return False
    return bool(
        semantic.rank is not None
        or semantic.descriptor not in {None, "unknown"}
        or semantic.destination_query
    )


def _temporal_is_unanchored_week(semantic: Any, current_message: str) -> bool:
    if getattr(semantic, "operation", "none") in {"none", "clear"}:
        return False
    normalized = normalize_vietnamese_alias(current_message)
    flexible_week = (
        "any day of the week" in normalized
        or "anytime this week" in normalized
        or "ngay nao trong tuan" in normalized
        or "bat cu ngay nao trong tuan" in normalized
    )
    anchored = any(
        marker in normalized for marker in ("this week", "next week", "tuan nay", "tuan sau")
    )
    return flexible_week and not anchored


def _confidence_question(field: str) -> tuple[str, str]:
    questions = {
        "temporal": ("Bạn muốn đi vào ngày hoặc tuần nào?", "Which date or week would you like?"),
        "date_window": (
            "Bạn muốn đi vào ngày hoặc tuần nào?",
            "Which date or week would you like?",
        ),
        "origin": ("Bạn sẽ khởi hành từ đâu?", "Where will you depart from?"),
        "destination": (
            "Bạn muốn đến đâu, hay muốn tôi gợi ý?",
            "Where would you like to go, or should I suggest somewhere?",
        ),
        "budget": ("Bạn muốn thay đổi ngân sách thành bao nhiêu?", "What should the budget be?"),
        "passengers": (
            "Vui lòng cho biết số người lớn, trẻ em và em bé.",
            "Please specify the adults, children, and infants.",
        ),
        "result_reference": (
            "Bạn muốn chọn số mấy trong các lựa chọn hiện có?",
            "Which option number would you like to choose?",
        ),
    }
    return questions.get(
        field,
        (
            "Tôi chưa hiểu chính xác phần này. Bạn có thể nói rõ điều kiện cần thay đổi không?",
            "I could not interpret that constraint confidently. What would you like to change?",
        ),
    )


def _ensure_confident(field: str, semantic: Any, *, current_message: str) -> None:
    if field == "temporal" and _temporal_is_unanchored_week(semantic, current_message):
        raise SemanticPolicyRejectionError(
            reason="ambiguous_week_anchor",
            field="date_window",
            question_vi="Bạn muốn đi vào ngày bất kỳ trong tuần này hay tuần sau?",
            question_en="Would you like any day this week or any day next week?",
            missing_fields=("date_window",),
        )
    if getattr(semantic, "operation", "set") != "none" and semantic.confidence < 0.85:
        question_vi, question_en = _confidence_question(field)
        raise SemanticPolicyRejectionError(
            reason="low_semantic_confidence",
            field=field,
            question_vi=question_vi,
            question_en=question_en,
            missing_fields=(field,),
        )


def _ensure_source(field: str, semantic: Any, current_message: str) -> None:
    source_text = semantic.source_text
    if source_text is not None and not _source_is_current_message(source_text, current_message):
        raise SemanticPolicyRejectionError(
            reason="source_not_in_current_message",
            field=field,
            question_vi="Tôi chỉ áp dụng thông tin được nói trong tin nhắn hiện tại. Bạn muốn thay đổi điều kiện nào?",
            question_en="I can only apply information expressed in the current message. Which constraint should change?",
            missing_fields=(field,),
        )


def _validate_semantic_object(field: str, semantic: Any, current_message: str) -> None:
    _ensure_confident(field, semantic, current_message=current_message)
    if getattr(semantic, "source_text", None) is not None:
        _ensure_source(field, semantic, current_message)


def _targets_inspiration_options(current_message: str) -> bool:
    normalized = normalize_vietnamese_alias(current_message)
    lookup_terms = (
        "find",
        "search",
        "look up",
        "show",
        "view",
        "tim",
        "tim kiem",
        "tra cuu",
        "xem",
    )
    flight_terms = (
        "flight",
        "flights",
        "airfare",
        "ticket",
        "tickets",
        "chuyen bay",
        "ve may bay",
    )
    return "tim ve" in normalized or (
        any(term in normalized for term in lookup_terms)
        and any(term in normalized for term in flight_terms)
    )


def _reference_items(
    safe_context: Mapping[str, object], *, inspiration_only: bool = False
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    presented = safe_context.get("presented_offers_v1")
    if not inspiration_only and isinstance(presented, Mapping):
        offers = presented.get("offers")
        if isinstance(offers, list):
            candidates.extend(item for item in offers if isinstance(item, dict))
    inspiration = safe_context.get("trip_inspiration_v1")
    if isinstance(inspiration, Mapping):
        options = inspiration.get("options")
        if isinstance(options, list):
            candidates.extend(item for item in options if isinstance(item, dict))
    return candidates


def _resolve_presented_reference(
    semantic: PresentedResultReferenceSemantic,
    safe_context: Mapping[str, object],
    *,
    current_message: str,
) -> int | None:
    inspiration_items = _reference_items(safe_context, inspiration_only=True)
    items = (
        inspiration_items
        if inspiration_items and _targets_inspiration_options(current_message)
        else _reference_items(safe_context)
    )
    if not items:
        raise SemanticPolicyRejectionError(
            reason="invalid_pending_context",
            field="result_reference",
            question_vi="Hiện chưa có lựa chọn nào được lưu trong cuộc trò chuyện để tôi tham chiếu.",
            question_en="There are no current-thread results available to reference.",
            missing_fields=("presented_result",),
        )
    if semantic.rank is not None:
        matches = [item for item in items if item.get("rank") == semantic.rank]
    elif semantic.descriptor == "cheapest":
        matches = [item for item in items if item.get("rank") == 1]
    elif semantic.descriptor == "previous":
        matches = items
    else:
        matches = []
    if len(matches) == 1:
        return int(matches[0]["rank"])
    if len(matches) > 1:
        raise SemanticPolicyRejectionError(
            reason="ambiguous_reference",
            field="result_reference",
            question_vi="Có nhiều lựa chọn phù hợp. Bạn muốn chọn số mấy?",
            question_en="Several presented results match. Which option number do you want?",
            missing_fields=("presented_result",),
        )
    raise SemanticPolicyRejectionError(
        reason="ambiguous_reference",
        field="result_reference",
        question_vi="Tôi không tìm thấy đúng lựa chọn bạn đang nói đến. Vui lòng nêu số lựa chọn.",
        question_en="I could not match that result reference. Please provide an option number.",
        missing_fields=("presented_result",),
    )


def apply_semantic_updates(
    *,
    current_message: str,
    plan: Any,
    safe_context: Mapping[str, object] | None = None,
    clock: Clock,
    timezone: str | None = None,
) -> ValidatedSemanticUpdates:
    """Validate planner semantics and resolve only safe, non-transactional values.

    This function has no persistence or provider side effects. Specialist services
    receive its bounded result and remain the only owners of checkpoint projection.
    """

    updates = plan
    if hasattr(plan, "semantic_updates"):
        updates = plan.semantic_updates
    if not isinstance(updates, SemanticConstraintUpdates):
        updates = SemanticConstraintUpdates.model_validate(updates or {})
    safe = safe_context if isinstance(safe_context, Mapping) else {}
    for field, semantic in (
        ("temporal", updates.temporal),
        ("budget", updates.budget),
        ("passengers", updates.passengers),
        ("origin", updates.origin),
        ("destination", updates.destination),
        ("search", updates.search),
        ("result_reference", updates.result_reference),
    ):
        if semantic is not None and not (
            field == "result_reference" and not _has_meaningful_result_reference(semantic)
        ):
            _validate_semantic_object(field, semantic, current_message)
            if field == "search" and semantic.optimization is not None:
                _validate_semantic_object(
                    "search",
                    semantic.optimization,
                    current_message,
                )

    protected_clear_fields = {
        "temporal": ("date_window", "date_window"),
        "origin": ("origin", "origin"),
        "passengers": ("passengers", "passengers"),
    }
    for field, (field_name, missing_field) in protected_clear_fields.items():
        semantic = getattr(updates, field)
        if semantic is not None and semantic.operation == "clear":
            raise SemanticPolicyRejectionError(
                reason="protected_constraint_clear",
                field=field_name,
                question_vi="Tôi không thể tự xoá thông tin hành trình đã xác nhận. Bạn muốn thay đổi phần nào?",
                question_en="I cannot silently clear a confirmed trip constraint. Which part would you like to change?",
                missing_fields=(missing_field,),
            )

    active_timezone = timezone or TripDiscoverySettings().default_timezone
    temporal_window = None
    if updates.temporal is not None and updates.temporal.operation not in {"none", "clear"}:
        resolver = DateResolutionService(clock=clock, timezone=active_timezone)
        try:
            temporal_window = resolver.resolve_semantic(
                updates.temporal,
                original_message=current_message,
            )
        except AmbiguousDateError as exc:
            raise SemanticPolicyRejectionError(
                reason="ambiguous_temporal_reference",
                field="date_window",
                question_vi="Bạn muốn chọn ngày này trong tuần hiện tại hay tuần sau?",
                question_en="Do you mean this weekday in the current week or next week?",
                missing_fields=("date_window",),
            ) from exc
        except DateResolutionError as exc:
            raise SemanticPolicyRejectionError(
                reason="deterministic_resolution_failed",
                field="date_window",
                question_vi="Vui lòng cho biết một ngày đi hoặc khoảng ngày hợp lệ, tối đa bảy ngày.",
                question_en="Please provide a valid departure date or a window of up to seven days.",
                missing_fields=("date_window",),
            ) from exc

    meaningful_reference = (
        updates.result_reference
        if _has_meaningful_result_reference(updates.result_reference)
        else None
    )
    referenced_rank = None
    if meaningful_reference is not None:
        referenced_rank = _resolve_presented_reference(
            meaningful_reference, safe, current_message=current_message
        )

    changed: list[str] = []
    for field, semantic in (
        ("temporal", updates.temporal),
        ("budget", updates.budget),
        ("passengers", updates.passengers),
        ("origin", updates.origin),
        ("destination", updates.destination),
        ("search", updates.search),
    ):
        if semantic is not None and semantic.operation != "none":
            changed.append(field)
    if updates.search is not None and updates.search.cabin is not None:
        changed.append("cabin")

    if updates.passengers is not None and updates.passengers.operation not in {"none", "clear"}:
        passenger = updates.passengers
        if passenger.total_only is not None:
            raise SemanticPolicyRejectionError(
                reason="ambiguous_passenger_composition",
                field="passengers",
                question_vi="Gia đình có tổng số người này gồm bao nhiêu người lớn, trẻ em và em bé?",
                question_en="How many adults, children, and infants are in that total?",
                missing_fields=("passengers",),
            )
        values = {
            "adults": passenger.adults if passenger.adults is not None else 1,
            "children": passenger.children or 0,
            "infants": passenger.infants or 0,
        }
        try:
            PassengerMix(**values)
        except ValueError as exc:
            raise SemanticPolicyRejectionError(
                reason="ambiguous_passenger_composition",
                field="passengers",
                question_vi="Số lượng hành khách chưa hợp lệ. Vui lòng cho biết số người lớn, trẻ em và em bé.",
                question_en="That passenger mix is not valid. Please specify adults, children, and infants.",
                missing_fields=("passengers",),
            ) from exc

    return ValidatedSemanticUpdates(
        temporal_window=temporal_window,
        temporal=updates.temporal,
        budget=updates.budget,
        passengers=updates.passengers,
        destination=updates.destination,
        origin=updates.origin,
        search=updates.search,
        result_reference=meaningful_reference,
        referenced_rank=referenced_rank,
        changed_fields=tuple(dict.fromkeys(changed)),
    )


__all__ = [
    "SemanticPolicyRejectionError",
    "ValidatedSemanticUpdates",
    "apply_semantic_updates",
]
