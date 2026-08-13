from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import UserRecord, UserSessionRecord
from agent_system.repositories.sessions import SessionRepository


class SessionAuthenticationError(ValueError):
    pass


class CSRFValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SessionSettings:
    lifetime: timedelta = timedelta(hours=12)
    cookie_name: str = "flight_session"
    cookie_secure: bool = True
    cookie_same_site: str = "lax"
    cookie_path: str = "/"


@dataclass(frozen=True, repr=False)
class SessionCredentials:
    session_token: str
    csrf_token: str
    expires_at: datetime

    def __repr__(self) -> str:
        return f"SessionCredentials(session_token=<redacted>, csrf_token=<redacted>, expires_at={self.expires_at!r})"


class SessionTokenHasher:
    def __init__(self, pepper: bytes) -> None:
        if len(pepper) < 32:
            raise ValueError("session token pepper must be at least 32 bytes")
        self._pepper = pepper

    @classmethod
    def from_environment(cls) -> SessionTokenHasher:
        encoded = os.environ.get("SESSION_TOKEN_PEPPER")
        if not encoded:
            raise RuntimeError("SESSION_TOKEN_PEPPER is required")
        try:
            pepper = base64.urlsafe_b64decode(encoded)
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError("SESSION_TOKEN_PEPPER is invalid") from exc
        return cls(pepper)

    def hash(self, token: str) -> str:
        return hmac.new(self._pepper, token.encode(), hashlib.sha256).hexdigest()


class SessionService:
    def __init__(
        self,
        repository: SessionRepository,
        hasher: SessionTokenHasher,
        settings: SessionSettings | None = None,
    ) -> None:
        self.repository = repository
        self.hasher = hasher
        self.settings = settings or SessionSettings()

    def create(
        self,
        user: UserRecord,
        *,
        now: datetime,
        device_label: str | None = None,
        user_agent: str | None = None,
    ) -> SessionCredentials:
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = now + self.settings.lifetime
        user_agent_hash = hashlib.sha256(user_agent.encode()).hexdigest() if user_agent else None
        self.repository.add(
            UserSessionRecord(
                user_id=user.id,
                session_token_hash=self.hasher.hash(session_token),
                csrf_token_hash=self.hasher.hash(csrf_token),
                issued_at=now,
                expires_at=expires_at,
                device_label=device_label,
                user_agent_hash=user_agent_hash,
            )
        )
        return SessionCredentials(session_token, csrf_token, expires_at)

    def authenticate(
        self,
        session_token: str | None,
        *,
        now: datetime | None = None,
    ) -> AuthenticatedPrincipal:
        if not session_token:
            raise SessionAuthenticationError("authentication required")
        now = now or datetime.now(UTC)
        found = self.repository.get_active_by_hash(self.hasher.hash(session_token), now=now)
        if found is None:
            raise SessionAuthenticationError("authentication required")
        session, user = found
        session.last_seen_at = now
        return AuthenticatedPrincipal(
            user_id=user.id,
            issuer=user.oidc_issuer,
            subject=user.oidc_subject,
            session_id=session.id,
        )

    def verify_csrf(
        self,
        session_token: str | None,
        csrf_token: str | None,
        *,
        now: datetime | None = None,
    ) -> AuthenticatedPrincipal:
        now = now or datetime.now(UTC)
        principal = self.authenticate(session_token, now=now)
        found = self.repository.get_active_by_hash(self.hasher.hash(session_token or ""), now=now)
        if found is None or not csrf_token:
            raise CSRFValidationError("CSRF validation failed")
        session, _ = found
        if not hmac.compare_digest(session.csrf_token_hash, self.hasher.hash(csrf_token)):
            raise CSRFValidationError("CSRF validation failed")
        return principal

    def revoke(
        self,
        session_token: str | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        if not session_token:
            return False
        return self.repository.revoke_by_hash(
            self.hasher.hash(session_token), now=now or datetime.now(UTC)
        )
