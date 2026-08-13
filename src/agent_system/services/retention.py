from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from agent_system.db.models import (
    BookingIntentRecord,
    BookingQuoteRecord,
    FlightOfferRecord,
    FlightSearchAttemptRecord,
    FlightSearchRecord,
    WatchMatchRecord,
)


@dataclass(frozen=True)
class ExpiredSearchCleanupResult:
    """Counts from one bounded cleanup batch."""

    offers_deleted: int
    attempts_deleted: int
    searches_deleted: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cleanup times must be timezone-aware")
    return value.astimezone(UTC)


def _rowcount(result) -> int:
    value = result.rowcount
    return max(0, int(value)) if value is not None else 0


class SearchRetentionService:
    """Remove expired search projections without touching transactional records.

    The job is deliberately bounded and database-only. An expired offer remains
    when a booking intent, quote, or watch match references it, because those
    records are part of the transactional/audit history and use restrictive
    ownership references.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def purge_expired(
        self,
        *,
        now: datetime | None = None,
        batch_size: int = 500,
    ) -> ExpiredSearchCleanupResult:
        if not 1 <= batch_size <= 5_000:
            raise ValueError("batch_size must be between 1 and 5000")
        cutoff = _utc(now or datetime.now(UTC))

        protected_by_intent = exists(
            select(1)
            .select_from(BookingIntentRecord)
            .where(
                BookingIntentRecord.source_offer_id == FlightOfferRecord.id,
                BookingIntentRecord.user_id == FlightOfferRecord.user_id,
            )
            .correlate(FlightOfferRecord)
        )
        protected_by_quote = exists(
            select(1)
            .select_from(BookingQuoteRecord)
            .where(
                BookingQuoteRecord.source_offer_id == FlightOfferRecord.id,
                BookingQuoteRecord.user_id == FlightOfferRecord.user_id,
            )
            .correlate(FlightOfferRecord)
        )
        protected_by_watch_match = exists(
            select(1)
            .select_from(WatchMatchRecord)
            .where(
                WatchMatchRecord.source_offer_id == FlightOfferRecord.id,
                WatchMatchRecord.user_id == FlightOfferRecord.user_id,
            )
            .correlate(FlightOfferRecord)
        )
        expired_offer_ids = self.session.scalars(
            select(FlightOfferRecord.id)
            .where(
                FlightOfferRecord.expires_at <= cutoff,
                ~protected_by_intent,
                ~protected_by_quote,
                ~protected_by_watch_match,
            )
            .limit(batch_size)
        ).all()
        offers_deleted = 0
        if expired_offer_ids:
            offers_deleted = _rowcount(
                self.session.execute(
                    delete(FlightOfferRecord).where(FlightOfferRecord.id.in_(expired_offer_ids))
                )
            )

        # A search is removable only after all of its offers are gone. Attempts
        # are deleted first to keep the operation valid on every supported DB.
        removable_search_ids = self.session.scalars(
            select(FlightSearchRecord.id)
            .where(
                FlightSearchRecord.expires_at <= cutoff,
                ~exists(select(1).where(FlightOfferRecord.search_id == FlightSearchRecord.id)),
            )
            .limit(batch_size)
        ).all()
        attempts_deleted = 0
        searches_deleted = 0
        if removable_search_ids:
            attempts_deleted += _rowcount(
                self.session.execute(
                    delete(FlightSearchAttemptRecord).where(
                        FlightSearchAttemptRecord.search_id.in_(removable_search_ids)
                    )
                )
            )
            searches_deleted = _rowcount(
                self.session.execute(
                    delete(FlightSearchRecord).where(
                        FlightSearchRecord.id.in_(removable_search_ids)
                    )
                )
            )

        # Failed/no-result attempts have no search_id. They are safe to remove
        # after their own completed timestamp crosses the same retention point.
        orphan_attempt_ids = self.session.scalars(
            select(FlightSearchAttemptRecord.id)
            .where(
                FlightSearchAttemptRecord.search_id.is_(None),
                FlightSearchAttemptRecord.completed_at <= cutoff,
            )
            .limit(batch_size)
        ).all()
        if orphan_attempt_ids:
            attempts_deleted += _rowcount(
                self.session.execute(
                    delete(FlightSearchAttemptRecord).where(
                        FlightSearchAttemptRecord.id.in_(orphan_attempt_ids)
                    )
                )
            )

        return ExpiredSearchCleanupResult(
            offers_deleted=offers_deleted,
            attempts_deleted=attempts_deleted,
            searches_deleted=searches_deleted,
        )


__all__ = ["ExpiredSearchCleanupResult", "SearchRetentionService"]
