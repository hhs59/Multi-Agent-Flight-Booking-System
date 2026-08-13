from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import timedelta

from pydantic import JsonValue, SecretStr

from agent_system.domain.provider_services import (
    NotificationDestination,
    NotificationResult,
)
from agent_system.domain.values import ExecutionMode, ProviderMetadata
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.errors import IdempotencyConflictError


class MockNotificationProvider:
    name = "mock"
    environment = ExecutionMode.MOCK

    def __init__(self, *, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self.accepted_messages: list[dict[str, JsonValue]] = []
        self._results: dict[str, tuple[str, NotificationResult]] = {}

    async def send(
        self,
        template: str,
        destination: NotificationDestination,
        idempotency_key: str,
        *,
        variables: Mapping[str, JsonValue] | None = None,
    ) -> NotificationResult:
        destination_hash = hashlib.sha256(
            destination.address.get_secret_value().encode()
        ).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "template": template,
                    "channel": destination.channel,
                    "destination_hash": destination_hash,
                    "variables": variables or {},
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        existing = self._results.get(idempotency_key)
        if existing is not None:
            if existing[0] != fingerprint:
                raise IdempotencyConflictError(
                    provider=self.name,
                    operation="send",
                    safe_message="idempotency key was reused with different input",
                )
            return existing[1]
        now = self.clock.now()
        result = NotificationResult(
            metadata=ProviderMetadata(
                provider=self.name,
                environment=self.environment,
                is_live=False,
                retrieved_at=now,
                expires_at=now + timedelta(minutes=15),
            ),
            accepted=True,
            provider_message_reference=SecretStr(f"SYN-MSG-{idempotency_key}"),
        )
        self.accepted_messages.append(
            {
                "template": template,
                "channel": destination.channel.value,
                "destination_hash": destination_hash,
                "variables": dict(variables or {}),
            }
        )
        self._results[idempotency_key] = (fingerprint, result)
        return result
