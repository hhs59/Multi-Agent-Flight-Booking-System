from __future__ import annotations

from datetime import UTC, datetime

from agent_system.auth.oidc import OIDCVerifier
from agent_system.auth.sessions import SessionCredentials, SessionService
from agent_system.db.models import UserRecord
from agent_system.repositories.users import UserRepository


class IdentityService:
    def __init__(
        self,
        verifier: OIDCVerifier,
        users: UserRepository,
        sessions: SessionService,
    ) -> None:
        self.verifier = verifier
        self.users = users
        self.sessions = sessions

    def exchange_oidc_token(
        self,
        token: str,
        *,
        now: datetime | None = None,
        device_label: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[UserRecord, SessionCredentials]:
        identity = self.verifier.verify(token)
        user = self.users.provision(identity)
        if user.status != "active":
            raise ValueError("account is not active")
        credentials = self.sessions.create(
            user,
            now=now or datetime.now(UTC),
            device_label=device_label,
            user_agent=user_agent,
        )
        return user, credentials
