from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from agent_system.db.models import (
    AuditEventRecord,
    BookingEventRecord,
    BookingIntentRecord,
    BookingOperationRecord,
    BookingQuoteRecord,
    BookingRecord,
    FlightDiscoveryRecord,
    FlightOfferRecord,
    FlightSearchAttemptRecord,
    FlightSearchRecord,
    FlightWatchRecord,
    OutboxEventRecord,
    PurchaseMandateRecord,
    TravelerProfileRecord,
    UserSessionRecord,
    WatchHoldRecord,
    WatchMatchRecord,
    WatchNotificationRecord,
    WatchRunRecord,
)
from agent_system.repositories.base import OwnedRepository, OwnershipViolationError


class UserSessionRepository(OwnedRepository[UserSessionRecord]):
    model = UserSessionRecord


class FlightSearchRepository(OwnedRepository[FlightSearchRecord]):
    model = FlightSearchRecord


class FlightDiscoveryRepository(OwnedRepository[FlightDiscoveryRecord]):
    model = FlightDiscoveryRecord


class FlightSearchAttemptRepository(OwnedRepository[FlightSearchAttemptRecord]):
    model = FlightSearchAttemptRecord


class FlightOfferRepository(OwnedRepository[FlightOfferRecord]):
    model = FlightOfferRecord


class BookingIntentRepository(OwnedRepository[BookingIntentRecord]):
    model = BookingIntentRecord

    def _require_owned_travelers(self, traveler_profile_ids: list[str]) -> None:
        try:
            profile_ids = {UUID(profile_id) for profile_id in traveler_profile_ids}
        except (TypeError, ValueError) as exc:
            raise OwnershipViolationError("traveler profile references are invalid") from exc
        owned_count = self.session.scalar(
            select(func.count())
            .select_from(TravelerProfileRecord)
            .where(
                TravelerProfileRecord.user_id == self.principal.user_id,
                TravelerProfileRecord.id.in_(profile_ids),
            )
        )
        if owned_count != len(profile_ids) or len(profile_ids) != len(traveler_profile_ids):
            raise OwnershipViolationError("traveler profiles must belong to the authenticated user")

    def add(self, record: BookingIntentRecord) -> BookingIntentRecord:
        self._require_owned_travelers(record.traveler_profile_ids)
        return super().add(record)

    def update_fields(
        self,
        resource_id: UUID,
        *,
        expected_version: int | None = None,
        **values: Any,
    ) -> BookingIntentRecord:
        if "traveler_profile_ids" in values:
            self._require_owned_travelers(values["traveler_profile_ids"])
        return super().update_fields(
            resource_id,
            expected_version=expected_version,
            **values,
        )


class BookingRepository(OwnedRepository[BookingRecord]):
    model = BookingRecord


class BookingQuoteRepository(OwnedRepository[BookingQuoteRecord]):
    model = BookingQuoteRecord


class BookingOperationRepository(OwnedRepository[BookingOperationRecord]):
    model = BookingOperationRecord


class PurchaseMandateRepository(OwnedRepository[PurchaseMandateRecord]):
    model = PurchaseMandateRecord


class WatchHoldRepository(OwnedRepository[WatchHoldRecord]):
    model = WatchHoldRecord


class WatchNotificationRepository(OwnedRepository[WatchNotificationRecord]):
    model = WatchNotificationRecord


class BookingEventRepository(OwnedRepository[BookingEventRecord]):
    model = BookingEventRecord


class FlightWatchRepository(OwnedRepository[FlightWatchRecord]):
    model = FlightWatchRecord


class WatchRunRepository(OwnedRepository[WatchRunRecord]):
    model = WatchRunRecord


class WatchMatchRepository(OwnedRepository[WatchMatchRecord]):
    model = WatchMatchRecord


class AuditEventOwnedRepository(OwnedRepository[AuditEventRecord]):
    model = AuditEventRecord


class OutboxOwnedRepository(OwnedRepository[OutboxEventRecord]):
    model = OutboxEventRecord
