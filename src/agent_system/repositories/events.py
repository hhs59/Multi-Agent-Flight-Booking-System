from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import AuditEventRecord, OutboxEventRecord
from agent_system.security.sanitization import sanitize_payload


class AuditRepository:
    def __init__(self, session: Session, principal: AuthenticatedPrincipal) -> None:
        self.session = session
        self.principal = principal

    def record(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: UUID | None,
        metadata: dict[str, Any],
        occurred_at: datetime,
    ) -> AuditEventRecord:
        event = AuditEventRecord(
            user_id=self.principal.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=sanitize_payload(metadata),
            occurred_at=occurred_at,
        )
        self.session.add(event)
        self.session.flush()
        return event


class OutboxRepository:
    def __init__(self, session: Session, principal: AuthenticatedPrincipal) -> None:
        self.session = session
        self.principal = principal

    def enqueue(
        self,
        *,
        topic: str,
        aggregate_type: str,
        aggregate_id: UUID,
        payload: dict[str, Any],
        idempotency_key: str,
        available_at: datetime,
    ) -> OutboxEventRecord:
        event = OutboxEventRecord(
            user_id=self.principal.user_id,
            topic=topic,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=sanitize_payload(payload),
            idempotency_key=idempotency_key,
            available_at=available_at,
        )
        self.session.add(event)
        self.session.flush()
        return event
