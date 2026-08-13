from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from decimal import Decimal
from threading import Lock
from typing import Protocol

from agent_system.domain.values import ExecutionMode


@dataclass(frozen=True)
class ProviderCallMetric:
    provider: str
    environment: ExecutionMode
    operation: str
    outcome: str
    latency_ms: Decimal
    retry_count: int
    error_class: str | None = None


class ProviderMetricSink(Protocol):
    def record(self, metric: ProviderCallMetric) -> None: ...


class LoggingProviderMetricSink:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("agent_system.providers")

    def record(self, metric: ProviderCallMetric) -> None:
        self.logger.info("provider_call", extra={"provider_metric": asdict(metric)})


class InMemoryProviderMetricSink:
    def __init__(self) -> None:
        self._metrics: list[ProviderCallMetric] = []
        self._lock = Lock()

    def record(self, metric: ProviderCallMetric) -> None:
        with self._lock:
            self._metrics.append(metric)

    @property
    def metrics(self) -> tuple[ProviderCallMetric, ...]:
        with self._lock:
            return tuple(self._metrics)
