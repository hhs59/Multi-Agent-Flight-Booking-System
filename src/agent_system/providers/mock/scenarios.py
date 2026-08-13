from enum import StrEnum


class MockScenario(StrEnum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    PRICE_CHANGED = "price_changed"
    EXPIRED_OFFER = "expired_offer"
    UNAVAILABLE_SEGMENT = "unavailable_segment"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    HOLD_SUPPORTED = "hold_supported"
    HOLD_UNSUPPORTED = "hold_unsupported"
    PAYMENT_SUCCESS = "payment_success"
    PAYMENT_DECLINED = "payment_declined"
    PAYMENT_REQUIRES_ACTION = "payment_requires_action"
    DUPLICATE_IDEMPOTENCY = "duplicate_idempotency"
    BOOKING_SUPPORTED = "booking_supported"
