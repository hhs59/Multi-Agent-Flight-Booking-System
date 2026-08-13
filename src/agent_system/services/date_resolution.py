from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent_system.domain.trip_discovery import DatePrecision, TravelDateWindow
from agent_system.providers.clock import Clock

DEFAULT_TRAVEL_TIMEZONE = "Asia/Ho_Chi_Minh"


class DateResolutionError(ValueError):
    """A user date cannot be converted into a safe absolute window."""


class PastDateError(DateResolutionError):
    """A requested date is before local today."""


class DateWindowTooWideError(DateResolutionError):
    """A requested date window exceeds the first-release seven-day bound."""


class AmbiguousDateError(DateResolutionError):
    """Multiple dates were supplied without explicit flexible-range language."""


@dataclass(frozen=True, init=False)
class TripDiscoverySettings:
    default_timezone: str = DEFAULT_TRAVEL_TIMEZONE

    def __init__(
        self,
        default_timezone: str = DEFAULT_TRAVEL_TIMEZONE,
        *,
        timezone: str | None = None,
    ) -> None:
        if timezone is not None:
            if default_timezone != DEFAULT_TRAVEL_TIMEZONE and default_timezone != timezone:
                raise ValueError("default_timezone and timezone disagree")
            default_timezone = timezone
        normalized = default_timezone.strip()
        if not normalized:
            raise ValueError("TRAVEL_DEFAULT_TIMEZONE cannot be blank")
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {normalized}") from exc
        object.__setattr__(self, "default_timezone", normalized)

    @property
    def timezone(self) -> str:
        return self.default_timezone

    @classmethod
    def from_environment(cls) -> TripDiscoverySettings:
        return cls(os.getenv("TRAVEL_DEFAULT_TIMEZONE", DEFAULT_TRAVEL_TIMEZONE))


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold().strip())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_marks.replace("đ", "d").split())


def _clock_now(clock: Clock) -> datetime:
    now = clock.now()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("date-resolution clocks must return timezone-aware instants")
    return now


def _explicit_dates(text: str) -> list[date]:
    found: list[tuple[int, date]] = []
    for match in re.finditer(r"(?<!\w)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\w)", text):
        try:
            found.append((match.start(), date(*map(int, match.groups()))))
        except ValueError as exc:
            raise DateResolutionError("an explicit ISO date is invalid") from exc
    for match in re.finditer(r"(?<!\w)(\d{1,2})[/-](\d{1,2})[/-](20\d{2})(?!\w)", text):
        day, month, year = map(int, match.groups())
        try:
            found.append((match.start(), date(year, month, day)))
        except ValueError as exc:
            raise DateResolutionError("an explicit localized date is invalid") from exc
    return [value for _, value in sorted(found, key=lambda item: item[0])]


def _has(normalized: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized) is not None


def _is_flexible_range(normalized: str) -> bool:
    if any(_has(normalized, phrase) for phrase in ("between", "anytime", "flexible", "bat ky")):
        return True
    return (
        (_has(normalized, "from") and _has(normalized, "to"))
        or (_has(normalized, "tu") and _has(normalized, "den"))
        or _has(normalized, "trong khoang")
    )


def _window(
    start_date: date,
    end_date: date,
    *,
    precision: DatePrecision,
    timezone: str,
) -> TravelDateWindow:
    days = (end_date - start_date).days + 1
    if days > 7:
        raise DateWindowTooWideError("travel date window cannot exceed seven inclusive days")
    return TravelDateWindow(
        start_date=start_date,
        end_date=end_date,
        precision=precision,
        timezone=timezone,
    )


@dataclass(frozen=True)
class DateResolutionService:
    clock: Clock
    timezone: str

    def __post_init__(self) -> None:
        normalized = self.timezone.strip()
        if not normalized:
            raise ValueError("date-resolution timezone cannot be blank")
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {normalized}") from exc
        object.__setattr__(self, "timezone", normalized)

    def _today(self) -> date:
        return _clock_now(self.clock).astimezone(ZoneInfo(self.timezone)).date()

    def local_today(self) -> date:
        return self._today()

    def resolve_semantic(self, semantic, *, original_message: str) -> TravelDateWindow | None:
        """Resolve a validated linguistic temporal label using the server clock.

        The planner may describe relative time, but it never supplies an absolute
        date. Explicit dates continue through the ordinary parser so digits in the
        user message, rather than model output, remain authoritative.
        """

        from agent_system.domain.orchestration import TemporalSemantic

        if not isinstance(semantic, TemporalSemantic):
            semantic = TemporalSemantic.model_validate(semantic)
        if semantic.operation in {"none", "clear"} or semantic.kind == "unknown":
            return None

        today = self._today()
        kind = semantic.kind
        flexibility = semantic.flexibility
        if kind == "today":
            return _window(today, today, precision=DatePrecision.EXACT, timezone=self.timezone)
        if kind == "tomorrow":
            target = today + timedelta(days=1)
            return _window(target, target, precision=DatePrecision.EXACT, timezone=self.timezone)
        if kind == "this_week":
            end = today + timedelta(days=6 - today.weekday())
            precision = DatePrecision.FLEXIBLE if flexibility == "any_day" else DatePrecision.WEEK
            return _window(today, end, precision=precision, timezone=self.timezone)
        if kind == "next_week":
            start = today + timedelta(days=7 - today.weekday())
            precision = DatePrecision.FLEXIBLE if flexibility == "any_day" else DatePrecision.WEEK
            return _window(
                start, start + timedelta(days=6), precision=precision, timezone=self.timezone
            )
        if kind in {"this_weekend", "next_weekend"}:
            if kind == "next_weekend":
                next_monday = today + timedelta(days=7 - today.weekday())
                start = next_monday + timedelta(days=5)
            elif today.weekday() == 6:
                start = today
            else:
                start = today + timedelta(days=(5 - today.weekday()) % 7)
            end = start + timedelta(days=1) if start.weekday() == 5 else start
            return _window(start, end, precision=DatePrecision.FLEXIBLE, timezone=self.timezone)
        if kind == "weekday":
            if semantic.weekday is None:
                raise DateResolutionError("weekday semantics require a weekday")
            weekday_index = {
                "monday": 0,
                "tuesday": 1,
                "wednesday": 2,
                "thursday": 3,
                "friday": 4,
                "saturday": 5,
                "sunday": 6,
            }[semantic.weekday]
            if semantic.week_offset == 1:
                start = today + timedelta(days=7 - today.weekday() + weekday_index)
            else:
                delta = weekday_index - today.weekday()
                if delta < 0:
                    raise AmbiguousDateError("weekday has passed; next week must be specified")
                start = today + timedelta(days=delta)
            return _window(start, start, precision=DatePrecision.EXACT, timezone=self.timezone)
        if kind == "relative_days":
            if semantic.relative_days is None:
                raise DateResolutionError("relative-day semantics require a day count")
            if semantic.relative_days > 7:
                raise DateWindowTooWideError(
                    "relative date windows cannot exceed seven inclusive days"
                )
            target = today + timedelta(days=semantic.relative_days)
            includes_today = _normalize(original_message).__contains__("from today") or _normalize(
                original_message
            ).__contains__("tu hom nay")
            if flexibility == "exact":
                return _window(
                    target, target, precision=DatePrecision.EXACT, timezone=self.timezone
                )
            start = today if includes_today else today + timedelta(days=1)
            return _window(start, target, precision=DatePrecision.RANGE, timezone=self.timezone)
        if kind in {"explicit_date_text", "explicit_range_text"}:
            resolved = self.resolve(original_message)
            if resolved is None:
                raise DateResolutionError("explicit temporal semantics contain no user date")
            return resolved
        raise DateResolutionError("unsupported temporal semantic")

    def resolve(self, message: str, *, locale: str = "en") -> TravelDateWindow | None:
        if not isinstance(message, str) or not message.strip():
            return None

        del locale  # Date recognition is deliberately language-marker driven.
        today = self._today()
        explicit = _explicit_dates(message)
        normalized = _normalize(message)

        if len(explicit) > 2:
            raise AmbiguousDateError("more than two explicit dates were supplied")
        if len(explicit) == 2:
            if not _is_flexible_range(normalized):
                raise AmbiguousDateError(
                    "two explicit dates require language that indicates a flexible range"
                )
            start_date, end_date = explicit
            if end_date < start_date:
                raise DateResolutionError("date range ends before it starts")
            if start_date < today or end_date < today:
                raise PastDateError("date range contains a past date")
            return _window(
                start_date,
                end_date,
                precision=DatePrecision.RANGE,
                timezone=self.timezone,
            )
        if len(explicit) == 1:
            if explicit[0] < today:
                raise PastDateError("requested date is before local today")
            return _window(
                explicit[0],
                explicit[0],
                precision=DatePrecision.EXACT,
                timezone=self.timezone,
            )

        if _has(normalized, "this week") or _has(normalized, "tuan nay"):
            end_date = today + timedelta(days=6 - today.weekday())
            return _window(
                today,
                end_date,
                precision=DatePrecision.FLEXIBLE,
                timezone=self.timezone,
            )

        if _has(normalized, "next week") or _has(normalized, "tuan sau"):
            days_until_next_monday = 7 - today.weekday()
            start_date = today + timedelta(days=days_until_next_monday)
            return _window(
                start_date,
                start_date + timedelta(days=6),
                precision=DatePrecision.WEEK,
                timezone=self.timezone,
            )

        if _has(normalized, "next weekend") or _has(normalized, "cuoi tuan sau"):
            next_monday = today + timedelta(days=7 - today.weekday())
            start_date = next_monday + timedelta(days=5)
            return _window(
                start_date,
                start_date + timedelta(days=1),
                precision=DatePrecision.FLEXIBLE,
                timezone=self.timezone,
            )

        if _has(normalized, "this weekend") or _has(normalized, "cuoi tuan nay"):
            if today.weekday() == 6:
                start_date = today
                end_date = today
            else:
                start_date = today + timedelta(days=(5 - today.weekday()) % 7)
                end_date = start_date + timedelta(days=1)
            return _window(
                start_date,
                end_date,
                precision=DatePrecision.FLEXIBLE,
                timezone=self.timezone,
            )

        relative = re.search(
            r"(?<!\w)(?:in|sau|trong)\s+(\d{1,3})\s+(?:days?|ngay)(?:\s+(?:from now|toi))?(?!\w)",
            normalized,
        )
        if relative:
            days = int(relative.group(1))
            if not 1 <= days <= 365:
                raise DateResolutionError("relative dates must be between one and 365 days")
            target = today + timedelta(days=days)
            return _window(
                target,
                target,
                precision=DatePrecision.EXACT,
                timezone=self.timezone,
            )

        if _has(normalized, "tomorrow") or _has(normalized, "ngay mai"):
            target = today + timedelta(days=1)
            return _window(
                target,
                target,
                precision=DatePrecision.EXACT,
                timezone=self.timezone,
            )

        if _has(normalized, "today") or _has(normalized, "hom nay"):
            return _window(
                today,
                today,
                precision=DatePrecision.EXACT,
                timezone=self.timezone,
            )

        weekday_names = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
            "thu hai": 0,
            "thu ba": 1,
            "thu tu": 2,
            "thu nam": 3,
            "thu sau": 4,
            "thu bay": 5,
            "chu nhat": 6,
        }
        for name, weekday in weekday_names.items():
            if not _has(normalized, name):
                continue
            delta = weekday - today.weekday()
            if delta < 0:
                raise AmbiguousDateError("weekday has passed; next week must be specified")
            target = today + timedelta(days=delta)
            return _window(
                target,
                target,
                precision=DatePrecision.EXACT,
                timezone=self.timezone,
            )

        return None

    parse = resolve


def resolve_date_window(
    message: str,
    *,
    clock: Clock,
    timezone: str,
    locale: str = "en",
) -> TravelDateWindow | None:
    return DateResolutionService(clock=clock, timezone=timezone).resolve(
        message,
        locale=locale,
    )


DateResolver = DateResolutionService
