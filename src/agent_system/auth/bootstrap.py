from __future__ import annotations

import os
from datetime import timedelta

from agent_system.auth.oidc import HttpJwksProvider, OIDCVerifier, OIDCVerifierSettings
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


def auth_runtime_from_environment() -> AuthRuntime:
    issuer = _required_environment("OIDC_ISSUER").rstrip("/")
    audience = _required_environment("OIDC_AUDIENCE")
    jwks_url = _required_environment("OIDC_JWKS_URL")
    session_hours = int(os.environ.get("SESSION_LIFETIME_HOURS", "12"))
    if session_hours < 1 or session_hours > 168:
        raise RuntimeError("SESSION_LIFETIME_HOURS must be between 1 and 168")

    engine = engine_from_environment()
    verifier = OIDCVerifier(
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
