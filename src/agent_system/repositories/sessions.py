from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agent_system.db.models import UserRecord, UserSessionRecord


class SessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: UserSessionRecord) -> UserSessionRecord:
        self.session.add(record)
        self.session.flush()
        return record

    def get_active_by_hash(
        self,
        token_hash: str,
        *,
        now: datetime,
    ) -> tuple[UserSessionRecord, UserRecord] | None:
        statement = (
            select(UserSessionRecord, UserRecord)
            .join(UserRecord, UserRecord.id == UserSessionRecord.user_id)
            .where(
                UserSessionRecord.session_token_hash == token_hash,
                UserSessionRecord.revoked_at.is_(None),
                UserSessionRecord.expires_at > now,
                UserRecord.status == "active",
            )
        )
        row = self.session.execute(statement).one_or_none()
        return (row[0], row[1]) if row else None

    def revoke_by_hash(self, token_hash: str, *, now: datetime) -> bool:
        result = self.session.execute(
            update(UserSessionRecord)
            .where(
                UserSessionRecord.session_token_hash == token_hash,
                UserSessionRecord.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return result.rowcount == 1

    def revoke_all_for_user(self, user_id, *, now: datetime) -> int:
        result = self.session.execute(
            update(UserSessionRecord)
            .where(
                UserSessionRecord.user_id == user_id,
                UserSessionRecord.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return result.rowcount
