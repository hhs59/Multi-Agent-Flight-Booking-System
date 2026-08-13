from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from threading import Lock
from typing import TypeVar

from agent_system.domain.values import ExecutionMode
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.errors import (
    CircuitOpenError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from agent_system.providers.settings import ProviderSettings
from agent_system.providers.telemetry import (
    LoggingProviderMetricSink,
    ProviderCallMetric,
    ProviderMetricSink,
)

T = TypeVar("T")
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.1
    maximum_delay_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 6:
            raise ValueError("max_attempts must be between one and six")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds cannot be negative")
        if self.maximum_delay_seconds < self.base_delay_seconds:
            raise ValueError("maximum delay cannot be less than base delay")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitHealth:
    provider: str
    environment: ExecutionMode
    operation: str
    state: CircuitState


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout: timedelta = timedelta(seconds=30),
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout <= timedelta(0):
            raise ValueError("recovery_timeout must be positive")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failures = 0
        self._opened_at: datetime | None = None
        self._state = CircuitState.CLOSED
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def before_call(
        self,
        *,
        now: datetime,
        provider: str,
        operation: str,
    ) -> None:
        with self._lock:
            if self._state is CircuitState.CLOSED:
                return
            if (
                self._state is CircuitState.OPEN
                and self._opened_at is not None
                and now >= self._opened_at + self.recovery_timeout
            ):
                self._state = CircuitState.HALF_OPEN
                return
            raise CircuitOpenError(
                provider=provider,
                operation=operation,
                safe_message="provider circuit is open",
            )

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._state = CircuitState.CLOSED

    def record_failure(self, *, now: datetime) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold or self._state is CircuitState.HALF_OPEN:
                self._opened_at = now
                self._state = CircuitState.OPEN


class ProviderExecutor:
    def __init__(
        self,
        *,
        environment: ExecutionMode,
        total_timeout_seconds: float,
        retry_policy: RetryPolicy | None = None,
        clock: Clock | None = None,
        sleeper: Sleep = asyncio.sleep,
        metric_sink: ProviderMetricSink | None = None,
        circuit_failure_threshold: int = 3,
        circuit_recovery_timeout: timedelta = timedelta(seconds=30),
    ) -> None:
        if total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be positive")
        self.environment = environment
        self.total_timeout_seconds = total_timeout_seconds
        self.retry_policy = retry_policy or RetryPolicy()
        self.clock = clock or SystemClock()
        self.sleeper = sleeper
        self.metric_sink = metric_sink or LoggingProviderMetricSink()
        self.circuit_failure_threshold = circuit_failure_threshold
        self.circuit_recovery_timeout = circuit_recovery_timeout
        self._breakers: dict[tuple[str, ExecutionMode, str], CircuitBreaker] = {}
        self._lock = Lock()

    @classmethod
    def from_settings(
        cls,
        settings: ProviderSettings,
        *,
        clock: Clock | None = None,
        sleeper: Sleep = asyncio.sleep,
        metric_sink: ProviderMetricSink | None = None,
    ) -> ProviderExecutor:
        return cls(
            environment=settings.execution_mode,
            total_timeout_seconds=settings.total_timeout_seconds,
            retry_policy=RetryPolicy(max_attempts=settings.max_safe_retries + 1),
            clock=clock,
            sleeper=sleeper,
            metric_sink=metric_sink,
        )

    @property
    def circuit_health(self) -> tuple[CircuitHealth, ...]:
        with self._lock:
            items = tuple(self._breakers.items())
        return tuple(
            CircuitHealth(
                provider=provider,
                environment=environment,
                operation=operation,
                state=breaker.state,
            )
            for (provider, environment, operation), breaker in sorted(
                items,
                key=lambda item: item[0],
            )
        )

    def _breaker(self, provider: str, operation: str) -> CircuitBreaker:
        key = (provider, self.environment, operation)
        with self._lock:
            breaker = self._breakers.get(key)
            if breaker is None:
                breaker = CircuitBreaker(
                    failure_threshold=self.circuit_failure_threshold,
                    recovery_timeout=self.circuit_recovery_timeout,
                )
                self._breakers[key] = breaker
            return breaker

    async def execute(
        self,
        *,
        provider: str,
        operation: str,
        call: Callable[[], Awaitable[T]],
        retry_safe: bool,
    ) -> T:
        started = time.monotonic()
        retries = 0
        outcome = "error"
        error_class: str | None = None
        breaker = self._breaker(provider, operation)
        try:
            breaker.before_call(
                now=self.clock.now(),
                provider=provider,
                operation=operation,
            )
            async with asyncio.timeout(self.total_timeout_seconds):
                for attempt in range(self.retry_policy.max_attempts):
                    try:
                        result = await call()
                    except ProviderError as exc:
                        error_class = type(exc).__name__
                        should_retry = (
                            retry_safe
                            and exc.retryable
                            and attempt + 1 < self.retry_policy.max_attempts
                        )
                        if not should_retry:
                            if exc.retryable:
                                breaker.record_failure(now=self.clock.now())
                            raise
                        retries += 1
                        delay = min(
                            self.retry_policy.base_delay_seconds * (2**attempt),
                            self.retry_policy.maximum_delay_seconds,
                        )
                        if isinstance(exc, ProviderRateLimitError):
                            delay = min(
                                exc.retry_after_seconds
                                if exc.retry_after_seconds is not None
                                else delay,
                                self.retry_policy.maximum_delay_seconds,
                            )
                        await self.sleeper(delay)
                    else:
                        breaker.record_success()
                        outcome = "success"
                        return result
        except TimeoutError as exc:
            error_class = "ProviderTimeoutError"
            breaker.record_failure(now=self.clock.now())
            raise ProviderTimeoutError(
                provider=provider,
                operation=operation,
                safe_message="provider operation exceeded its total deadline",
            ) from exc
        except ProviderError as exc:
            error_class = error_class or type(exc).__name__
            raise
        finally:
            elapsed = Decimal(str((time.monotonic() - started) * 1000)).quantize(Decimal("0.001"))
            self.metric_sink.record(
                ProviderCallMetric(
                    provider=provider,
                    environment=self.environment,
                    operation=operation,
                    outcome=outcome,
                    latency_ms=elapsed,
                    retry_count=retries,
                    error_class=error_class,
                )
            )
        raise RuntimeError("provider execution ended without a result")
