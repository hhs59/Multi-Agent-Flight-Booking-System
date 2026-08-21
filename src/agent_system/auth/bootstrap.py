from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from agent_system.auth.oidc import (
    HttpJwksProvider,
    JwksProvider,
    OIDCIdentity,
    OIDCValidationError,
    OIDCVerifier,
    OIDCVerifierSettings,
)
from agent_system.auth.router import AuthRuntime
from agent_system.auth.sessions import SessionSettings, SessionTokenHasher
from agent_system.db.session import build_session_factory, engine_from_environment
from agent_system.security.encryption import FieldEncryptor


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required when identity is enabled")
    return value


def _environment_flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    if raw.lower() not in {"true", "false"}:
        raise RuntimeError(f"{name} must be true or false")
    return raw.lower() == "true"


class _LocalDevJwksProvider:
    """Dummy JWKS provider for local dev — always returns empty keys."""

    def get_jwks(self) -> dict[str, Any]:
        return {"keys": []}


class _LocalDevOIDCVerifier(OIDCVerifier):
    """
    A permissive OIDC verifier for local development.
    It does NOT validate real OIDC tokens; instead it accepts any non-empty token
    and returns a fixed local identity. Real deployments must use the proper verifier.
    """

    def __init__(self) -> None:
        super().__init__(
            OIDCVerifierSettings(
                issuer="local",
                audience="local",
                require_email_verified=False,
            ),
            _LocalDevJwksProvider(),
        )

    def verify(self, token: str) -> OIDCIdentity:  # type: ignore[override]
        if not token:
            raise OIDCValidationError("missing token")
        # Accept any token in local dev mode; return a fixed default identity
        return OIDCIdentity(
            issuer="local",
            subject="default_user",
            email="demo@example.test",
            display_name="Demo Traveler",
            email_verified=True,
        )


def auth_runtime_from_environment() -> AuthRuntime:
    """
    Build AuthRuntime from environment.

    If OIDC_ISSUER / OIDC_JWKS_URL are not set (local dev without Keycloak),
    returns a local dev runtime with a permissive verifier. This allows the full
    product API to be available without a real OIDC server.
    """
    issuer = os.environ.get("OIDC_ISSUER", "").strip()
    jwks_url = os.environ.get("OIDC_JWKS_URL", "").strip()
    audience = os.environ.get("OIDC_AUDIENCE", "local").strip()

    session_hours = int(os.environ.get("SESSION_LIFETIME_HOURS", "12"))
    if session_hours < 1 or session_hours > 168:
        raise RuntimeError("SESSION_LIFETIME_HOURS must be between 1 and 168")

    engine = engine_from_environment()

    if issuer and jwks_url:
        # Full OIDC mode (production / staging with Keycloak or another OIDC provider)
        verifier: OIDCVerifier = OIDCVerifier(
            OIDCVerifierSettings(
                issuer=issuer,
                audience=audience,
                allow_test_email_domains=_environment_flag("OIDC_ALLOW_TEST_EMAIL_DOMAINS"),
            ),
            HttpJwksProvider(
                jwks_url,
                allow_http_for_local=_environment_flag("OIDC_ALLOW_HTTP_FOR_LOCAL"),
            ),
        )
    else:
        # Local dev mode — no Keycloak required
        verifier = _LocalDevOIDCVerifier()

    settings = SessionSettings(
        lifetime=timedelta(hours=session_hours),
        cookie_name=os.environ.get("SESSION_COOKIE_NAME", "flight_session"),
        cookie_secure=_environment_flag("SESSION_COOKIE_SECURE", default=True),
        cookie_same_site=os.environ.get("SESSION_COOKIE_SAME_SITE", "lax"),
        cookie_path="/",
    )
    if settings.cookie_same_site not in {"lax", "strict", "none"}:
        raise RuntimeError("SESSION_COOKIE_SAME_SITE must be lax, strict, or none")
    if settings.cookie_same_site == "none" and not settings.cookie_secure:
        raise RuntimeError("SameSite=None requires a Secure session cookie")

    return AuthRuntime(
        session_factory=build_session_factory(engine),
        verifier=verifier,
        token_hasher=SessionTokenHasher.from_environment(),
        session_settings=settings,
        encryptor=FieldEncryptor.from_environment(),
    )
