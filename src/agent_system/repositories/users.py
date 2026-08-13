from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_system.auth.oidc import OIDCIdentity
from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import UserRecord
from agent_system.repositories.base import ResourceNotFoundError


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_identity(self, issuer: str, subject: str) -> UserRecord | None:
        return self.session.scalar(
            select(UserRecord).where(
                UserRecord.oidc_issuer == issuer,
                UserRecord.oidc_subject == subject,
            )
        )

    def provision(self, identity: OIDCIdentity) -> UserRecord:
        user = self.get_by_identity(identity.issuer, identity.subject)
        if user is None:
            user = UserRecord(
                oidc_issuer=identity.issuer,
                oidc_subject=identity.subject,
                email=identity.email.lower(),
                display_name=identity.display_name,
                locale="vi",
                timezone="Asia/Ho_Chi_Minh",
                status="active",
            )
            try:
                with self.session.begin_nested():
                    self.session.add(user)
                    self.session.flush()
            except IntegrityError:
                user = self.get_by_identity(identity.issuer, identity.subject)
                if user is None:
                    raise
        else:
            user.email = identity.email.lower()
            user.display_name = identity.display_name
        self.session.flush()
        return user

    def require_principal(self, principal: AuthenticatedPrincipal) -> UserRecord:
        user = self.session.scalar(
            select(UserRecord).where(
                UserRecord.id == principal.user_id,
                UserRecord.oidc_issuer == principal.issuer,
                UserRecord.oidc_subject == principal.subject,
            )
        )
        if user is None:
            raise ResourceNotFoundError("user was not found")
        return user
