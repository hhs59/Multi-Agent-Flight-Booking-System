from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.base import Base

RecordT = TypeVar("RecordT", bound=Base)


class OwnershipViolationError(ValueError):
    pass


class ResourceNotFoundError(LookupError):
    pass


class ConcurrencyConflictError(RuntimeError):
    pass


class OwnedRepository(Generic[RecordT]):
    model: type[RecordT]

    def __init__(self, session: Session, principal: AuthenticatedPrincipal) -> None:
        self.session = session
        self.principal = principal

    def add(self, record: RecordT) -> RecordT:
        if getattr(record, "user_id", None) != self.principal.user_id:
            raise OwnershipViolationError("owned record must use the authenticated principal")
        self.session.add(record)
        self.session.flush()
        return record

    def get(self, resource_id: UUID) -> RecordT | None:
        statement = select(self.model).where(
            self.model.id == resource_id,
            self.model.user_id == self.principal.user_id,
        )
        return self.session.scalar(statement)

    def require(self, resource_id: UUID) -> RecordT:
        record = self.get(resource_id)
        if record is None:
            raise ResourceNotFoundError("resource was not found")
        return record

    def list(self, *, limit: int = 100) -> Sequence[RecordT]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        statement = (
            select(self.model)
            .where(self.model.user_id == self.principal.user_id)
            .order_by(self.model.id)
            .limit(limit)
        )
        return self.session.scalars(statement).all()

    def update_fields(
        self,
        resource_id: UUID,
        *,
        expected_version: int | None = None,
        **values: Any,
    ) -> RecordT:
        forbidden = {"id", "user_id"}.intersection(values)
        if forbidden:
            raise OwnershipViolationError(f"cannot update ownership fields: {sorted(forbidden)}")
        statement = update(self.model).where(
            self.model.id == resource_id,
            self.model.user_id == self.principal.user_id,
        )
        if expected_version is not None:
            if not hasattr(self.model, "version"):
                raise ValueError("record does not support optimistic versioning")
            statement = statement.where(self.model.version == expected_version)
            values["version"] = expected_version + 1
        result = self.session.execute(statement.values(**values))
        if result.rowcount != 1:
            if expected_version is not None and self.get(resource_id) is not None:
                raise ConcurrencyConflictError("resource version changed")
            raise ResourceNotFoundError("resource was not found")
        self.session.flush()
        return self.require(resource_id)

    def delete(self, resource_id: UUID) -> bool:
        statement = delete(self.model).where(
            self.model.id == resource_id,
            self.model.user_id == self.principal.user_id,
        )
        result = self.session.execute(statement)
        return result.rowcount == 1
