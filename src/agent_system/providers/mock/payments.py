from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import TypeVar

from pydantic import SecretStr

from agent_system.domain.provider_services import (
    PaymentMethodSetupRequest,
    PaymentMethodSetupResult,
    PaymentResult,
    PaymentStatus,
)
from agent_system.domain.values import ExecutionMode, Money, ProviderMetadata
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.errors import (
    IdempotencyConflictError,
    ProviderValidationError,
)
from agent_system.providers.mock.scenarios import MockScenario

ResultT = TypeVar("ResultT", PaymentMethodSetupResult, PaymentResult)


class MockPaymentProvider:
    name = "mock"

    def __init__(
        self,
        *,
        environment: ExecutionMode = ExecutionMode.MOCK,
        scenario: MockScenario = MockScenario.PAYMENT_SUCCESS,
        clock: Clock | None = None,
    ) -> None:
        self.environment = environment
        self.scenario = scenario
        self.clock = clock or SystemClock()
        self._results: dict[tuple[str, str], tuple[str, object]] = {}
        self._amounts: dict[str, Money] = {}

    def _metadata(self) -> ProviderMetadata:
        now = self.clock.now()
        return ProviderMetadata(
            provider=self.name,
            environment=self.environment,
            is_live=False,
            retrieved_at=now,
            expires_at=now + timedelta(minutes=15),
        )

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def _idempotent(
        self,
        operation: str,
        key: str,
        fingerprint: str,
        factory: Callable[[], ResultT],
    ) -> ResultT:
        existing = self._results.get((operation, key))
        if existing is not None:
            if existing[0] != fingerprint:
                raise IdempotencyConflictError(
                    provider=self.name,
                    operation=operation,
                    safe_message="idempotency key was reused with different input",
                )
            return existing[1]  # type: ignore[return-value]
        result = factory()
        self._results[(operation, key)] = (fingerprint, result)
        return result

    async def setup_method(
        self,
        request: PaymentMethodSetupRequest,
        idempotency_key: str,
    ) -> PaymentMethodSetupResult:
        fingerprint = self._hash(request.provider_token.get_secret_value())

        def build() -> PaymentMethodSetupResult:
            if self.scenario is MockScenario.PAYMENT_REQUIRES_ACTION:
                return PaymentMethodSetupResult(
                    metadata=self._metadata(),
                    status=PaymentStatus.REQUIRES_ACTION,
                    action_reference=SecretStr(f"SYN-ACTION-{idempotency_key}"),
                )
            if self.scenario is MockScenario.PAYMENT_DECLINED:
                return PaymentMethodSetupResult(
                    metadata=self._metadata(),
                    status=PaymentStatus.DECLINED,
                    reason_code="synthetic_decline",
                )
            return PaymentMethodSetupResult(
                metadata=self._metadata(),
                status=PaymentStatus.READY,
                payment_method_reference=SecretStr(f"SYN-PM-{idempotency_key}"),
            )

        return self._idempotent("setup_method", idempotency_key, fingerprint, build)

    async def authorize(
        self,
        amount: Money,
        payment_method_reference: SecretStr,
        idempotency_key: str,
    ) -> PaymentResult:
        fingerprint = (
            f"{amount.amount}:{amount.currency}:"
            f"{self._hash(payment_method_reference.get_secret_value())}"
        )

        def build() -> PaymentResult:
            if self.scenario is MockScenario.PAYMENT_REQUIRES_ACTION:
                return PaymentResult(
                    metadata=self._metadata(),
                    status=PaymentStatus.REQUIRES_ACTION,
                    amount=amount,
                    action_reference=SecretStr(f"SYN-ACTION-{idempotency_key}"),
                )
            if self.scenario is MockScenario.PAYMENT_DECLINED:
                return PaymentResult(
                    metadata=self._metadata(),
                    status=PaymentStatus.DECLINED,
                    amount=amount,
                    reason_code="synthetic_decline",
                )
            reference = f"SYN-AUTH-{idempotency_key}"
            self._amounts[reference] = amount
            return PaymentResult(
                metadata=self._metadata(),
                status=PaymentStatus.AUTHORIZED,
                amount=amount,
                transaction_reference=SecretStr(reference),
            )

        return self._idempotent("authorize", idempotency_key, fingerprint, build)

    async def capture(
        self,
        authorization_reference: SecretStr,
        amount: Money,
        idempotency_key: str,
    ) -> PaymentResult:
        reference = authorization_reference.get_secret_value()
        fingerprint = f"{self._hash(reference)}:{amount.amount}:{amount.currency}"

        def build() -> PaymentResult:
            transaction = f"SYN-CAPTURE-{idempotency_key}"
            self._amounts[transaction] = amount
            return PaymentResult(
                metadata=self._metadata(),
                status=PaymentStatus.CAPTURED,
                amount=amount,
                transaction_reference=SecretStr(transaction),
            )

        return self._idempotent("capture", idempotency_key, fingerprint, build)

    async def cancel(
        self,
        authorization_reference: SecretStr,
        idempotency_key: str,
    ) -> PaymentResult:
        reference = authorization_reference.get_secret_value()
        amount = self._amounts.get(reference)
        if amount is None:
            raise ProviderValidationError(
                provider=self.name,
                operation="cancel",
                safe_message="unknown authorization reference",
            )

        def build() -> PaymentResult:
            return PaymentResult(
                metadata=self._metadata(),
                status=PaymentStatus.CANCELLED,
                amount=amount,
                transaction_reference=SecretStr(reference),
            )

        return self._idempotent(
            "cancel",
            idempotency_key,
            self._hash(reference),
            build,
        )

    async def refund(
        self,
        transaction_reference: SecretStr,
        amount: Money,
        idempotency_key: str,
    ) -> PaymentResult:
        reference = transaction_reference.get_secret_value()
        fingerprint = f"{self._hash(reference)}:{amount.amount}:{amount.currency}"

        def build() -> PaymentResult:
            return PaymentResult(
                metadata=self._metadata(),
                status=PaymentStatus.REFUNDED,
                amount=amount,
                transaction_reference=SecretStr(f"SYN-REFUND-{idempotency_key}"),
            )

        return self._idempotent("refund", idempotency_key, fingerprint, build)
