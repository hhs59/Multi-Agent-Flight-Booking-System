from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import jwt
from email_validator import EmailNotValidError, validate_email


class OIDCValidationError(ValueError):
    pass


@dataclass(frozen=True)
class OIDCIdentity:
    issuer: str
    subject: str
    email: str
    display_name: str
    email_verified: bool


@dataclass(frozen=True)
class OIDCVerifierSettings:
    issuer: str
    audience: str
    algorithms: tuple[str, ...] = ("RS256",)
    leeway_seconds: int = 30
    require_email_verified: bool = True
    allow_test_email_domains: bool = False


class JwksProvider(Protocol):
    def get_jwks(self) -> dict[str, Any]: ...


class StaticJwksProvider:
    def __init__(self, jwks: dict[str, Any]) -> None:
        self._jwks = jwks

    def get_jwks(self) -> dict[str, Any]:
        return self._jwks


class HttpJwksProvider:
    def __init__(
        self,
        jwks_url: str,
        *,
        cache_seconds: int = 300,
        timeout_seconds: float = 5.0,
        allow_http_for_local: bool = False,
    ) -> None:
        parsed_url = urlparse(jwks_url)
        is_local_http = (
            allow_http_for_local
            and parsed_url.scheme == "http"
            and parsed_url.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if parsed_url.scheme != "https" and not is_local_http:
            raise ValueError("OIDC JWKS URL must use HTTPS outside local development")
        self._url = jwks_url
        self._cache_seconds = cache_seconds
        self._timeout = timeout_seconds
        self._cached: dict[str, Any] | None = None
        self._cached_until = 0.0
        self._lock = threading.Lock()

    def get_jwks(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if self._cached is not None and now < self._cached_until:
                return self._cached
            response = httpx.get(self._url, timeout=self._timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload.get("keys"), list):
                raise OIDCValidationError("OIDC JWKS response has no keys")
            self._cached = payload
            self._cached_until = now + self._cache_seconds
            return payload


class OIDCVerifier:
    def __init__(self, settings: OIDCVerifierSettings, jwks_provider: JwksProvider) -> None:
        self._settings = settings
        self._jwks_provider = jwks_provider

    def verify(self, token: str) -> OIDCIdentity:
        if not token:
            raise OIDCValidationError("missing OIDC token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OIDCValidationError("invalid OIDC token header") from exc

        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in self._settings.algorithms:
            raise OIDCValidationError("OIDC token uses a disallowed signing algorithm")
        if not key_id:
            raise OIDCValidationError("OIDC token is missing key ID")

        jwks = self._jwks_provider.get_jwks()
        matching_key = next(
            (key for key in jwks.get("keys", []) if key.get("kid") == key_id),
            None,
        )
        if matching_key is None:
            raise OIDCValidationError("OIDC signing key is unknown")

        try:
            signing_key = jwt.PyJWK.from_dict(matching_key, algorithm=algorithm).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=list(self._settings.algorithms),
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                leeway=self._settings.leeway_seconds,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "email"]},
            )
        except jwt.PyJWTError as exc:
            raise OIDCValidationError("OIDC token validation failed") from exc

        email_verified = claims.get("email_verified") is True
        if self._settings.require_email_verified and not email_verified:
            raise OIDCValidationError("OIDC email is not verified")
        subject = claims.get("sub")
        email = claims.get("email")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise OIDCValidationError("OIDC subject is invalid")
        if not isinstance(email, str) or not email:
            raise OIDCValidationError("OIDC email is invalid")
        try:
            normalized_email = validate_email(
                email,
                check_deliverability=False,
                test_environment=self._settings.allow_test_email_domains,
            ).normalized
        except EmailNotValidError as exc:
            raise OIDCValidationError("OIDC email is invalid") from exc
        display_name = claims.get("name") or claims.get("preferred_username") or email
        if len(str(display_name)) > 200:
            raise OIDCValidationError("OIDC display name is invalid")
        return OIDCIdentity(
            issuer=self._settings.issuer,
            subject=subject,
            email=normalized_email,
            display_name=str(display_name),
            email_verified=email_verified,
        )
