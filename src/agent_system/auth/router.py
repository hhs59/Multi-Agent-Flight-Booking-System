from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from agent_system.auth.oidc import OIDCValidationError, OIDCVerifier
from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.auth.sessions import (
    CSRFValidationError,
    SessionAuthenticationError,
    SessionService,
    SessionSettings,
    SessionTokenHasher,
)
from agent_system.db.models import UserRecord
from agent_system.repositories.sessions import SessionRepository
from agent_system.repositories.users import UserRepository
from agent_system.security.encryption import FieldEncryptor
from agent_system.services.identity import IdentityService


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    display_name: str
    locale: str
    timezone: str
    csrf_token: str
    expires_at: datetime


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    email: str
    display_name: str
    locale: str
    timezone: str


@dataclass(frozen=True)
class AuthRuntime:
    session_factory: Callable[[], Session]
    verifier: OIDCVerifier
    token_hasher: SessionTokenHasher
    session_settings: SessionSettings
    encryptor: FieldEncryptor | None = None


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "valid OIDC bearer token required")
    return authorization.removeprefix("Bearer ").strip()


def create_auth_router(runtime: AuthRuntime) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["identity"])

    @router.post("/session", response_model=SessionResponse)
    def create_session(
        request: Request,
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> SessionResponse:
        database_session = runtime.session_factory()
        try:
            with database_session.begin():
                session_service = SessionService(
                    SessionRepository(database_session),
                    runtime.token_hasher,
                    runtime.session_settings,
                )
                identity_service = IdentityService(
                    runtime.verifier,
                    UserRepository(database_session),
                    session_service,
                )
                user, credentials = identity_service.exchange_oidc_token(
                    _bearer_token(authorization),
                    now=datetime.now(UTC),
                    user_agent=request.headers.get("user-agent"),
                )
        except (OIDCValidationError, ValueError) as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OIDC authentication failed") from exc
        finally:
            database_session.close()

        response.set_cookie(
            runtime.session_settings.cookie_name,
            credentials.session_token,
            httponly=True,
            secure=runtime.session_settings.cookie_secure,
            samesite=runtime.session_settings.cookie_same_site,
            path=runtime.session_settings.cookie_path,
            max_age=int(runtime.session_settings.lifetime.total_seconds()),
        )
        return SessionResponse(
            user_id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            locale=user.locale,
            timezone=user.timezone,
            csrf_token=credentials.csrf_token,
            expires_at=credentials.expires_at,
        )

    def authenticate_cookie(request: Request) -> tuple[AuthenticatedPrincipal, UserRecord]:
        database_session = runtime.session_factory()
        try:
            with database_session.begin():
                service = SessionService(
                    SessionRepository(database_session),
                    runtime.token_hasher,
                    runtime.session_settings,
                )
                principal = service.authenticate(
                    request.cookies.get(runtime.session_settings.cookie_name)
                )
                user = UserRepository(database_session).require_principal(principal)
                database_session.expunge(user)
                return principal, user
        except SessionAuthenticationError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required") from exc
        finally:
            database_session.close()

    @router.get("/me", response_model=CurrentUserResponse)
    def current_user(request: Request) -> CurrentUserResponse:
        _, user = authenticate_cookie(request)
        return CurrentUserResponse(
            user_id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            locale=user.locale,
            timezone=user.timezone,
        )

    @router.get("/login")
    def login_initiate():
        """Initiate OIDC login redirect."""
        return {
            "issuer": runtime.verifier.settings.issuer,
            "audience": runtime.verifier.settings.audience,
            "authorization_endpoint": f"{runtime.verifier.settings.issuer}/authorize",
            "response_type": "code",
            "scope": "openid profile email",
        }

    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(
        request: Request,
        response: Response,
        csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> None:
        session_token = request.cookies.get(runtime.session_settings.cookie_name)
        database_session = runtime.session_factory()
        try:
            with database_session.begin():
                service = SessionService(
                    SessionRepository(database_session),
                    runtime.token_hasher,
                    runtime.session_settings,
                )
                service.verify_csrf(session_token, csrf_token)
                service.revoke(session_token)
        except SessionAuthenticationError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required") from exc
        except CSRFValidationError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF validation failed") from exc
        finally:
            database_session.close()
        response.delete_cookie(
            runtime.session_settings.cookie_name,
            path=runtime.session_settings.cookie_path,
            secure=runtime.session_settings.cookie_secure,
            httponly=True,
            samesite=runtime.session_settings.cookie_same_site,
        )

    return router
