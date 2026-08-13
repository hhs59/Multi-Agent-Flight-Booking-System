from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr

from agent_system.domain.values import DomainModel


class BookingWorkflowStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_TRAVELERS = "needs_travelers"
    REPRICING = "repricing"
    QUOTE_READY = "quote_ready"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED_BY_USER = "confirmed_by_user"
    PAYMENT_AUTHORIZING = "payment_authorizing"
    CREATING_ORDER = "creating_order"
    TICKETING_PENDING = "ticketing_pending"
    BOOKED = "booked"
    NEEDS_USER_ACTION = "needs_user_action"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class BookingOperation(StrEnum):
    CONFIRM = "confirm"
    CANCEL = "cancel"
    REFUND = "refund"
    RECONCILE = "reconcile"


ALLOWED_BOOKING_TRANSITIONS: dict[BookingWorkflowStatus, frozenset[BookingWorkflowStatus]] = {
    BookingWorkflowStatus.DRAFT: frozenset(
        {
            BookingWorkflowStatus.NEEDS_TRAVELERS,
            BookingWorkflowStatus.REPRICING,
            BookingWorkflowStatus.FAILED,
        }
    ),
    BookingWorkflowStatus.NEEDS_TRAVELERS: frozenset(
        {BookingWorkflowStatus.REPRICING, BookingWorkflowStatus.FAILED}
    ),
    BookingWorkflowStatus.REPRICING: frozenset(
        {
            BookingWorkflowStatus.QUOTE_READY,
            BookingWorkflowStatus.AWAITING_CONFIRMATION,
            BookingWorkflowStatus.NEEDS_USER_ACTION,
            BookingWorkflowStatus.FAILED,
            BookingWorkflowStatus.EXPIRED,
        }
    ),
    BookingWorkflowStatus.QUOTE_READY: frozenset(
        {BookingWorkflowStatus.AWAITING_CONFIRMATION, BookingWorkflowStatus.EXPIRED}
    ),
    BookingWorkflowStatus.AWAITING_CONFIRMATION: frozenset(
        {
            BookingWorkflowStatus.CONFIRMED_BY_USER,
            BookingWorkflowStatus.REPRICING,
            BookingWorkflowStatus.NEEDS_USER_ACTION,
        }
    ),
    BookingWorkflowStatus.CONFIRMED_BY_USER: frozenset(
        {
            BookingWorkflowStatus.PAYMENT_AUTHORIZING,
            BookingWorkflowStatus.CREATING_ORDER,
            BookingWorkflowStatus.NEEDS_USER_ACTION,
        }
    ),
    BookingWorkflowStatus.PAYMENT_AUTHORIZING: frozenset(
        {
            BookingWorkflowStatus.CREATING_ORDER,
            BookingWorkflowStatus.NEEDS_USER_ACTION,
            BookingWorkflowStatus.FAILED,
        }
    ),
    BookingWorkflowStatus.CREATING_ORDER: frozenset(
        {
            BookingWorkflowStatus.TICKETING_PENDING,
            BookingWorkflowStatus.NEEDS_USER_ACTION,
            BookingWorkflowStatus.FAILED,
        }
    ),
    BookingWorkflowStatus.TICKETING_PENDING: frozenset(
        {
            BookingWorkflowStatus.BOOKED,
            BookingWorkflowStatus.NEEDS_USER_ACTION,
            BookingWorkflowStatus.CANCELLING,
        }
    ),
    BookingWorkflowStatus.BOOKED: frozenset({BookingWorkflowStatus.CANCELLING}),
    BookingWorkflowStatus.NEEDS_USER_ACTION: frozenset(
        {
            BookingWorkflowStatus.REPRICING,
            BookingWorkflowStatus.PAYMENT_AUTHORIZING,
            BookingWorkflowStatus.CREATING_ORDER,
            BookingWorkflowStatus.CANCELLING,
            BookingWorkflowStatus.FAILED,
            BookingWorkflowStatus.CANCELLED,
        }
    ),
    BookingWorkflowStatus.FAILED: frozenset(
        {BookingWorkflowStatus.REPRICING, BookingWorkflowStatus.CANCELLING}
    ),
    BookingWorkflowStatus.EXPIRED: frozenset({BookingWorkflowStatus.REPRICING}),
    BookingWorkflowStatus.CANCELLING: frozenset(
        {
            BookingWorkflowStatus.CANCELLED,
            BookingWorkflowStatus.NEEDS_USER_ACTION,
            BookingWorkflowStatus.FAILED,
        }
    ),
    BookingWorkflowStatus.CANCELLED: frozenset(),
}


def ensure_booking_transition(
    current: str,
    target: BookingWorkflowStatus,
) -> None:
    try:
        current_status = BookingWorkflowStatus(current)
    except ValueError as exc:
        raise ValueError(f"unknown booking workflow status: {current}") from exc
    if target not in ALLOWED_BOOKING_TRANSITIONS[current_status]:
        raise ValueError(f"invalid booking transition: {current_status.value} -> {target.value}")


class BookingPrepareRequest(DomainModel):
    traveler_profile_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=9)
    international: bool = False


class BookingConfirmationRequest(DomainModel):
    quote_version: int = Field(ge=1)
    acknowledged_fare_terms: bool
    payment_method_reference: SecretStr | None = None
    consent_scope: Literal["single_booking"] = "single_booking"


class BookingOperationRequest(DomainModel):
    confirmed: bool = False
