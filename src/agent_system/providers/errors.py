from __future__ import annotations

from agent_system.security.sanitization import sanitize_text


class ProviderError(RuntimeError):
    default_retryable = False

    def __init__(
        self,
        *,
        provider: str,
        operation: str,
        safe_message: str,
        retryable: bool | None = None,
        provider_code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.provider = sanitize_text(provider)[:80].lower()
        self.operation = sanitize_text(operation)[:80].lower()
        self.safe_message = sanitize_text(safe_message)[:500]
        self.retryable = self.default_retryable if retryable is None else retryable
        self.provider_code = sanitize_text(provider_code)[:80] if provider_code else None
        self.http_status = http_status
        details = f"{self.provider}.{self.operation}: {self.safe_message}"
        if self.provider_code:
            details = f"{details} [{self.provider_code}]"
        super().__init__(details)


class ProviderAuthenticationError(ProviderError):
    pass


class ProviderValidationError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    default_retryable = True

    def __init__(self, *, retry_after_seconds: float | None = None, **kwargs) -> None:
        if retry_after_seconds is not None and not 0 <= retry_after_seconds <= 300:
            raise ValueError("retry_after_seconds must be between 0 and 300")
        self.retry_after_seconds = retry_after_seconds
        super().__init__(**kwargs)


class ProviderTimeoutError(ProviderError):
    default_retryable = True


class ProviderUnavailableError(ProviderError):
    default_retryable = True


class ProviderMalformedResponseError(ProviderError):
    pass


class OfferExpiredError(ProviderError):
    pass


class OfferUnavailableError(ProviderError):
    pass


class PriceDiscrepancyError(ProviderError):
    pass


class CapabilityUnavailable(ProviderError):  # noqa: N818
    pass


class IdempotencyConflictError(ProviderError):
    pass


class CircuitOpenError(ProviderError):
    default_retryable = True
