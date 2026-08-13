from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Literal

from agent_system.domain.trip_discovery import (
    ClarificationChoice,
    ClarificationReason,
    ClarificationRequired,
    DiscoveryResult,
    DynamicDestinationChoice,
    DynamicDestinationChoices,
    DynamicOriginChoice,
    DynamicOriginChoices,
    ExecutableFlightSearch,
    LocationKind,
    LocationReference,
    PendingDestinationConfirmation,
    TravelDateWindow,
    TripDiscoveryCommand,
)
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.localization import AirportCatalog, LocationMention
from agent_system.services.date_resolution import (
    DateResolutionError,
    DateResolutionService,
    DateWindowTooWideError,
    PastDateError,
    TripDiscoverySettings,
)

_ACCEPTED_CONFIRMATIONS = frozenset(
    {
        "yes",
        "yes please",
        "ok",
        "okay",
        "yep",
        "sure",
        "correct",
        "thats right",
        "that's right",
        "sounds good",
        "go ahead",
        "dung",
        "dung roi",
        "vang",
        "duoc",
        "dong y",
        "oke",
    }
)
_REJECTED_CONFIRMATIONS = frozenset(
    {"no", "nope", "wrong", "not that", "khong", "khong phai", "sai roi"}
)


class TripDiscoveryService:
    """Resolve partial travel language without calling a provider."""

    def __init__(
        self,
        *,
        catalog: AirportCatalog | None = None,
        clock: Clock | None = None,
        settings: TripDiscoverySettings | None = None,
        date_resolution: DateResolutionService | None = None,
    ) -> None:
        self.catalog = catalog or AirportCatalog.from_v2_package_data()
        self.clock = clock or SystemClock()
        self.settings = settings or TripDiscoverySettings()
        self.date_resolution = date_resolution or DateResolutionService(
            clock=self.clock,
            timezone=self.settings.default_timezone,
        )

    @staticmethod
    def _projection_location(reference: LocationReference | None) -> dict[str, Any] | None:
        if reference is None:
            return None
        return {
            "kind": reference.kind.value,
            "normalized_name": reference.normalized_name,
            "airport_candidates": list(reference.airport_candidates),
            "country_code": reference.country_code,
            "city_id": reference.city_id,
        }

    @staticmethod
    def _projection_date(window: TravelDateWindow | None) -> dict[str, str] | None:
        if window is None:
            return None
        return {
            "start_date": window.start_date.isoformat(),
            "end_date": window.end_date.isoformat(),
            "precision": window.precision.value,
            "timezone": window.timezone,
        }

    @classmethod
    def _projection(
        cls,
        origin: LocationReference | None,
        destination: LocationReference | None,
        date_window: TravelDateWindow | None,
        pending_confirmation: PendingDestinationConfirmation | None = None,
        *,
        origin_resolution_source: Literal["catalog", "duffel", "fixture"] | None = None,
        destination_resolution_source: Literal["catalog", "duffel", "fixture"] | None = None,
        dynamic_destination_choices: DynamicDestinationChoices | None = None,
        dynamic_origin_choices: DynamicOriginChoices | None = None,
    ) -> dict[str, Any]:
        projection = {
            "origin": cls._projection_location(origin),
            "destination": cls._projection_location(destination),
            "date_window": cls._projection_date(date_window),
        }
        if pending_confirmation is not None and dynamic_destination_choices is None:
            projection["pending_destination_confirmation"] = pending_confirmation.model_dump(
                mode="json"
            )
        if origin_resolution_source is not None:
            projection["origin_resolution_source"] = origin_resolution_source
        if destination_resolution_source is not None:
            projection["destination_resolution_source"] = destination_resolution_source
        if dynamic_destination_choices is not None:
            projection["dynamic_destination_choices"] = dynamic_destination_choices.model_dump(
                mode="json"
            )
        if dynamic_origin_choices is not None:
            projection["dynamic_origin_choices"] = dynamic_origin_choices.model_dump(mode="json")
        return projection

    @staticmethod
    def _load_projection(
        safe_context: Mapping[str, Any] | None,
    ) -> tuple[
        LocationReference | None,
        LocationReference | None,
        TravelDateWindow | None,
        PendingDestinationConfirmation | None,
        Literal["catalog", "duffel", "fixture"] | None,
        Literal["catalog", "duffel", "fixture"] | None,
        DynamicDestinationChoices | None,
        DynamicOriginChoices | None,
    ]:
        if not isinstance(safe_context, Mapping):
            return None, None, None, None, None, None, None, None
        raw = safe_context.get("trip_discovery_v1")
        if not isinstance(raw, Mapping):
            return None, None, None, None, None, None, None, None
        try:
            origin = (
                LocationReference.model_validate(raw["origin"])
                if isinstance(raw.get("origin"), Mapping)
                else None
            )
            destination = (
                LocationReference.model_validate(raw["destination"])
                if isinstance(raw.get("destination"), Mapping)
                else None
            )
            date_window = (
                TravelDateWindow.model_validate(raw["date_window"])
                if isinstance(raw.get("date_window"), Mapping)
                else None
            )
        except (TypeError, ValueError):
            return None, None, None, None, None, None, None, None
        pending_confirmation = None
        raw_pending = raw.get("pending_destination_confirmation")
        if isinstance(raw_pending, Mapping):
            try:
                pending_confirmation = PendingDestinationConfirmation.model_validate(raw_pending)
            except (TypeError, ValueError):
                pending_confirmation = None
        source = raw.get("destination_resolution_source")
        if source not in {"catalog", "duffel", "fixture"}:
            source = None
        origin_source = raw.get("origin_resolution_source")
        if origin_source not in {"catalog", "duffel", "fixture"}:
            origin_source = None
        dynamic_choices = None
        raw_choices = raw.get("dynamic_destination_choices")
        if isinstance(raw_choices, Mapping):
            try:
                dynamic_choices = DynamicDestinationChoices.model_validate(raw_choices)
            except (TypeError, ValueError):
                dynamic_choices = None
        dynamic_origin_choices = None
        raw_origin_choices = raw.get("dynamic_origin_choices")
        if isinstance(raw_origin_choices, Mapping):
            try:
                dynamic_origin_choices = DynamicOriginChoices.model_validate(raw_origin_choices)
            except (TypeError, ValueError):
                dynamic_origin_choices = None
        return (
            origin,
            destination,
            date_window,
            pending_confirmation,
            origin_source,
            source,
            dynamic_choices,
            dynamic_origin_choices,
        )

    @staticmethod
    def _normalized_message(message: str) -> str:
        from agent_system.providers.localization import normalize_vietnamese_alias

        return normalize_vietnamese_alias(message)

    @classmethod
    def confirmation_action(cls, message: str) -> Literal["accept", "reject"] | None:
        """Parse only a standalone confirmation for a pending fuzzy destination."""

        normalized = cls._normalized_message(message).strip(" \t.,!?;:")
        if normalized in _ACCEPTED_CONFIRMATIONS:
            return "accept"
        if normalized in _REJECTED_CONFIRMATIONS:
            return "reject"
        return None

    @staticmethod
    def _before_marker(message: str, start: int, marker_pattern: str) -> bool:
        return re.search(rf"(?:^|\s)(?:{marker_pattern})\s*$", message[:start]) is not None

    def _location_roles(
        self,
        message: str,
    ) -> tuple[LocationMention | None, LocationMention | None]:
        normalized = self._normalized_message(message)
        mentions = self.catalog.find_mentions(message)
        if not mentions:
            return None, None

        origin: LocationMention | None = None
        destination: LocationMention | None = None
        if len(mentions) >= 2:
            first, second = mentions[0], mentions[1]
            between = normalized[first.end : second.start]
            if re.search(r"(?<!\w)(?:to|den)(?!\w)", between) or "->" in between:
                origin, destination = first, second
        if origin is None and self._before_marker(normalized, mentions[0].start, r"from|tu"):
            origin = mentions[0]
        for mention in mentions:
            if self._before_marker(normalized, mention.start, r"to|den|di|du lich|visit"):
                destination = mention
        if (
            destination is None
            and len(mentions) == 1
            and origin is None
            and (
                re.search(
                    r"(?<!\w)(?:go|travel|fly|visit)(?:\s+to)?\s*$",
                    normalized[: mentions[0].start],
                )
                or re.search(
                    r"(?<!\w)(?:di|du lich)\s*$",
                    normalized[: mentions[0].start],
                )
            )
        ):
            destination = mentions[0]
        if destination is None and len(mentions) >= 2 and origin is not None:
            destination = mentions[1]
        return origin, destination

    @staticmethod
    def _unknown_destination(message: str) -> LocationReference | None:
        normalized = TripDiscoveryService._normalized_message(message)
        patterns = (
            r"(?<!\w)(?:go|travel|fly)\s+(?:to\s+)?([^,.!?;]+)",
            r"(?<!\w)visit\s+([^,.!?;]+)",
            r"(?<!\w)(?:di|du lich|den|to)\s+([^,.!?;]+)",
        )
        stop_terms = (
            " next week",
            " this weekend",
            " tomorrow",
            " today",
            " tuan sau",
            " cuoi tuan nay",
            " ngay mai",
            " hom nay",
            " on ",
            " ngay ",
            " vao ",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if not match:
                continue
            candidate = match.group(1)
            for stop_term in stop_terms:
                candidate = candidate.split(stop_term, 1)[0]
            candidate = " ".join(candidate.split()).strip()
            if candidate:
                return LocationReference(
                    kind=LocationKind.UNKNOWN,
                    normalized_name=candidate[:160],
                )
        return None

    @staticmethod
    def _provider_reference_is_valid(reference: LocationReference) -> bool:
        if reference.kind not in {LocationKind.AIRPORT, LocationKind.CITY}:
            return False
        if reference.country_code is None or not reference.airport_candidates:
            return False
        if reference.kind is LocationKind.AIRPORT and len(reference.airport_candidates) != 1:
            return False
        if reference.kind is LocationKind.CITY and not reference.city_id:
            return False
        return len(set(reference.airport_candidates)) == len(reference.airport_candidates)

    def _canonical_reference(
        self,
        reference: LocationReference | None,
        *,
        dynamic_source: Literal["catalog", "duffel", "fixture"] | None = None,
    ) -> LocationReference | None:
        if reference is None:
            return None
        if reference.kind is LocationKind.UNKNOWN:
            return reference
        if dynamic_source in {"duffel", "fixture"}:
            return (
                reference
                if self._provider_reference_is_valid(reference)
                else LocationReference(
                    kind=LocationKind.UNKNOWN,
                    normalized_name=reference.normalized_name,
                )
            )
        if reference.kind is LocationKind.AIRPORT and reference.airport_candidates:
            resolved = self.catalog.resolve_location(reference.airport_candidates[0])
        else:
            resolved = self.catalog.resolve_location(reference.normalized_name)
        if resolved.kind is LocationKind.UNKNOWN or resolved.kind is not reference.kind:
            return LocationReference(
                kind=LocationKind.UNKNOWN,
                normalized_name=reference.normalized_name,
            )
        return resolved

    @classmethod
    def _accepts_any_dynamic_destination(cls, message: str) -> bool:
        normalized = cls._normalized_message(message).strip(" \t.,!?;:")
        return (
            re.search(
                r"(?<!\w)(?:anywhere|any city|any place|wherever|doesnt matter|"
                r"does not matter|no preference|you choose|surprise me|bat ky|"
                r"dau cung duoc|cho nao cung duoc|noi nao cung duoc|khong quan trong|mien la)(?!\w)",
                normalized,
            )
            is not None
        )

    @classmethod
    def dynamic_choice_action(
        cls,
        message: str,
        choices: DynamicDestinationChoices | Mapping[str, Any] | None,
        *,
        now: datetime | None = None,
    ) -> DynamicDestinationChoice | Literal["repeat"] | None:
        if choices is None:
            return None
        try:
            parsed = (
                choices
                if isinstance(choices, DynamicDestinationChoices)
                else DynamicDestinationChoices.model_validate(choices)
            )
        except (TypeError, ValueError):
            return None
        checked_at = now or datetime.now(parsed.expires_at.tzinfo)
        if parsed.expires_at <= checked_at:
            return None
        normalized = cls._normalized_message(message).strip(" \t.,!?;:")
        if cls.confirmation_action(message) == "accept":
            return "repeat"
        if cls._accepts_any_dynamic_destination(message):
            # Choices are server-owned and provider-ranked, so the first option is
            # a deterministic in-country selection rather than an LLM invention.
            return parsed.choices[0]
        number_match = re.fullmatch(
            r"(?:option|choice|number|lua chon|phuong an|chon|so)\s*#?\s*(\d{1,2})",
            normalized,
        )
        if number_match is not None:
            index = int(number_match.group(1))
            if 1 <= index <= len(parsed.choices):
                return parsed.choices[index - 1]
            return "repeat"
        for choice in parsed.choices:
            names = {
                cls._normalized_message(choice.value),
                cls._normalized_message(choice.label_en),
                cls._normalized_message(choice.label_vi),
                cls._normalized_message(choice.reference.normalized_name),
                *(cls._normalized_message(code) for code in choice.reference.airport_candidates),
            }
            if normalized in names:
                return choice
        return None

    @classmethod
    def dynamic_origin_choice_action(
        cls,
        message: str,
        choices: DynamicOriginChoices | Mapping[str, Any] | None,
        *,
        now: datetime | None = None,
    ) -> DynamicOriginChoice | Literal["repeat"] | None:
        if choices is None:
            return None
        try:
            parsed = (
                choices
                if isinstance(choices, DynamicOriginChoices)
                else DynamicOriginChoices.model_validate(choices)
            )
        except (TypeError, ValueError):
            return None
        normalized = cls._normalized_message(message).strip(" \t.,!?;:")
        number_match = re.fullmatch(
            r"(?:option|choice|number|lua chon|phuong an|chon|so)\s*#?\s*(\d{1,2})",
            normalized,
        )
        checked_at = now or datetime.now(parsed.expires_at.tzinfo)
        if parsed.expires_at <= checked_at:
            return "repeat" if number_match is not None else None
        if number_match is not None:
            index = int(number_match.group(1))
            if 1 <= index <= len(parsed.choices):
                return parsed.choices[index - 1]
            return "repeat"
        for choice in parsed.choices:
            names = {
                cls._normalized_message(choice.value),
                cls._normalized_message(choice.label_en),
                cls._normalized_message(choice.label_vi),
                cls._normalized_message(choice.reference.normalized_name),
                *(cls._normalized_message(code) for code in choice.reference.airport_candidates),
            }
            if normalized in names:
                return choice
        return None

    def _origin_from_trusted_preferences(
        self,
        *,
        safe_context: Mapping[str, Any] | None,
        trusted_preferences: Mapping[str, Any] | None,
    ) -> LocationReference | None:
        source: Mapping[str, Any] | None = trusted_preferences
        if source is None and isinstance(safe_context, Mapping):
            candidate = safe_context.get("trusted_preferences")
            if isinstance(candidate, Mapping):
                source = candidate
        if source is None and isinstance(safe_context, Mapping):
            candidate = safe_context.get("travel_preferences_v1")
            if isinstance(candidate, Mapping):
                source = candidate
        if source is None:
            return None
        raw = source.get("default_origin_airport")
        if not isinstance(raw, str) or not raw.strip():
            return None
        reference = self.catalog.resolve_location(raw)
        if reference.kind is not LocationKind.AIRPORT:
            return None
        return reference

    def _country_choices(self, reference: LocationReference) -> tuple[ClarificationChoice, ...]:
        choices: list[ClarificationChoice] = []
        for city in self.catalog.supported_city_references(reference.country_code or ""):
            codes = "/".join(city.airport_candidates)
            label = f"{city.normalized_name} ({codes})"
            choices.append(
                ClarificationChoice(
                    value=city.city_id or city.airport_candidates[0],
                    label_vi=label,
                    label_en=label,
                )
            )
        return tuple(choices[:10])

    def _origin_choices(self) -> tuple[ClarificationChoice, ...]:
        return tuple(
            ClarificationChoice(
                value=reference.airport_candidates[0],
                label_vi=reference.normalized_name,
                label_en=reference.normalized_name,
            )
            for reference in self.catalog.airport_references(limit=10)
        )

    @staticmethod
    def _clarification(
        *,
        reason: ClarificationReason,
        missing_fields: tuple[str, ...],
        question_vi: str,
        question_en: str,
        choices: tuple[ClarificationChoice, ...] = (),
    ) -> ClarificationRequired:
        return ClarificationRequired(
            reason=reason,
            missing_fields=missing_fields,
            question_vi=question_vi,
            question_en=question_en,
            choices=choices,
        )

    def _date_error_reason(self, error: DateResolutionError) -> ClarificationReason:
        if isinstance(error, DateWindowTooWideError):
            return ClarificationReason.DATE_WINDOW_TOO_WIDE
        if isinstance(error, PastDateError):
            return ClarificationReason.PAST_DATE
        return ClarificationReason.MISSING_DATES

    def resolve_with_projection(
        self,
        command: TripDiscoveryCommand | str | None = None,
        message: str = "",
        *,
        safe_context: Mapping[str, Any] | None = None,
        trusted_preferences: Mapping[str, Any] | None = None,
        safe_preferences: Mapping[str, Any] | None = None,
        default_origin_airport: str | None = None,
        preference_timezone: str | None = None,
        locale: str = "en",
        interpreted_origin: LocationReference | None = None,
        interpreted_origin_source: Literal["catalog", "duffel", "fixture"] | None = None,
        interpreted_destination: LocationReference | None = None,
        interpreted_source_text: str | None = None,
        interpreted_destination_source: Literal["catalog", "duffel", "fixture"] | None = None,
        interpreted_destination_requires_confirmation: bool = False,
        dynamic_destination_choices: tuple[DynamicDestinationChoice, ...] | None = None,
        dynamic_destination_source: Literal["catalog", "duffel", "fixture"] | None = None,
        dynamic_destination_kind: Literal["airport", "city", "country", "region", "unknown"]
        | None = None,
        dynamic_destination_interpretation: Literal[
            "exact", "probable", "uncertain", "unknown"
        ] = "exact",
        dynamic_destination_query: str | None = None,
        confirmation_action_override: Literal["accept", "reject"] | None = None,
        dynamic_choice_action_override: Literal["accept_any_destination", "continue_pending"]
        | None = None,
    ) -> tuple[DiscoveryResult, dict[str, Any]]:
        if isinstance(command, str):
            message = command
            command = TripDiscoveryCommand()
        elif command is None:
            command = TripDiscoveryCommand()
        (
            prior_origin,
            prior_destination,
            prior_dates,
            pending_confirmation,
            origin_source,
            destination_source,
            pending_dynamic_choices,
            pending_dynamic_origin_choices,
        ) = self._load_projection(safe_context)
        now = self.clock.now()
        if pending_dynamic_choices is not None and pending_dynamic_choices.expires_at <= now:
            pending_dynamic_choices = None
        if (
            pending_dynamic_origin_choices is not None
            and pending_dynamic_origin_choices.expires_at <= now
        ):
            pending_dynamic_origin_choices = None
        if dynamic_destination_choices is not None:
            # A new provider-resolved interpretation supersedes any stale typo
            # confirmation from an earlier turn.
            pending_confirmation = None
            if dynamic_destination_source not in {"catalog", "duffel", "fixture"}:
                raise ValueError("dynamic destination choices require a valid provider source")
            normalized_dynamic_query = (
                " ".join(dynamic_destination_query.strip().split())[:160]
                if isinstance(dynamic_destination_query, str) and dynamic_destination_query.strip()
                else None
            )
            pending_dynamic_choices = DynamicDestinationChoices(
                source=dynamic_destination_source,
                query_label=normalized_dynamic_query,
                expires_at=now + timedelta(minutes=30),
                choices=tuple(dynamic_destination_choices[:8]),
            )
            destination_source = dynamic_destination_source
        # The bounded checkpoint projection intentionally omits catalog metadata;
        # canonicalize it before using country-aware follow-up rules.
        prior_origin = self._canonical_reference(
            prior_origin,
            dynamic_source=origin_source,
        )
        prior_destination = self._canonical_reference(
            prior_destination,
            dynamic_source=destination_source,
        )
        if pending_confirmation is not None:
            pending_reference = self._canonical_reference(
                pending_confirmation.reference,
                dynamic_source=destination_source,
            )
            if pending_reference is None or pending_reference.kind is LocationKind.UNKNOWN:
                pending_confirmation = None
            else:
                pending_confirmation = PendingDestinationConfirmation(
                    original_text=pending_confirmation.original_text,
                    reference=pending_reference,
                )
        dynamic_selection = (
            pending_dynamic_choices.choices[0]
            if dynamic_choice_action_override == "accept_any_destination"
            and pending_dynamic_choices is not None
            else "repeat"
            if dynamic_choice_action_override == "continue_pending"
            and pending_dynamic_choices is not None
            else self.dynamic_choice_action(
                message,
                pending_dynamic_choices,
                now=now,
            )
        )
        dynamic_repeat = dynamic_selection == "repeat"
        selected_dynamic_destination = isinstance(dynamic_selection, DynamicDestinationChoice)
        if selected_dynamic_destination:
            command = command.model_copy(update={"destination": dynamic_selection.reference})
            pending_confirmation = None
            destination_source = pending_dynamic_choices.source if pending_dynamic_choices else None
            pending_dynamic_choices = None
        dynamic_origin_selection = self.dynamic_origin_choice_action(
            message,
            pending_dynamic_origin_choices,
            now=now,
        )
        if isinstance(dynamic_origin_selection, DynamicOriginChoice):
            command = command.model_copy(update={"origin": dynamic_origin_selection.reference})
            origin_source = (
                pending_dynamic_origin_choices.source if pending_dynamic_origin_choices else None
            )
            pending_dynamic_origin_choices = None
        action = (
            confirmation_action_override
            if pending_confirmation is not None
            and confirmation_action_override in {"accept", "reject"}
            else self.confirmation_action(message)
            if pending_confirmation is not None
            else None
        )
        parsed_origin_mention, parsed_destination_mention = self._location_roles(message)
        parsed_origin = (
            parsed_origin_mention.reference if parsed_origin_mention is not None else None
        )
        parsed_destination = (
            parsed_destination_mention.reference if parsed_destination_mention is not None else None
        )
        semantic_origin = self._canonical_reference(
            interpreted_origin,
            dynamic_source=interpreted_origin_source,
        )
        if semantic_origin is not None:
            # DeepSeek identifies the linguistic role; the catalog/provider only
            # canonicalizes that bounded place query. Do not rescan the full
            # sentence and replace a validated semantic origin.
            parsed_origin = semantic_origin
            parsed_origin_mention = None
            origin_source = interpreted_origin_source
        semantic_reference = self._canonical_reference(
            interpreted_destination,
            dynamic_source=interpreted_destination_source,
        )
        if semantic_reference is not None and semantic_reference.kind is LocationKind.UNKNOWN:
            semantic_reference = None
        has_semantic_destination = (
            semantic_reference is not None
            or dynamic_destination_choices is not None
            or selected_dynamic_destination
        )
        if has_semantic_destination:
            # Prevent natural text such as “Hàn Quốc” from being reinterpreted
            # as the lowercase IATA alias HAN after DeepSeek identifies it.
            parsed_destination = None
            parsed_destination_mention = None
        if semantic_reference is not None:
            pending_dynamic_choices = None
            if interpreted_destination_requires_confirmation:
                pending_confirmation = PendingDestinationConfirmation(
                    original_text=(
                        interpreted_source_text or semantic_reference.normalized_name
                    ).strip()[:160]
                    or "destination",
                    reference=semantic_reference,
                )
            else:
                parsed_destination = semantic_reference
                destination_source = interpreted_destination_source
                pending_confirmation = None
        if (
            pending_dynamic_choices is not None
            and dynamic_destination_choices is not None
            and not dynamic_repeat
            and pending_confirmation is None
        ):
            if (
                dynamic_destination_kind in {"country", "region"}
                or len(pending_dynamic_choices.choices) != 1
            ):
                parsed_destination = None
            else:
                selected_choice = pending_dynamic_choices.choices[0]
                destination_source = pending_dynamic_choices.source
                if dynamic_destination_interpretation in {"probable", "uncertain"}:
                    pending_confirmation = PendingDestinationConfirmation(
                        original_text=selected_choice.label_en,
                        reference=selected_choice.reference,
                    )
                    pending_dynamic_choices = None
                    parsed_destination = None
                else:
                    parsed_destination = selected_choice.reference
                    pending_dynamic_choices = None
        if (
            parsed_origin_mention is None
            and parsed_destination_mention is None
            and semantic_origin is None
            and not has_semantic_destination
        ):
            mentions = self.catalog.find_mentions(message)
            if len(mentions) == 1:
                candidate = mentions[0]
                if pending_confirmation is not None:
                    # A location explicitly supplied while confirmation is pending
                    # replaces the suggestion instead of being treated as an origin.
                    parsed_destination_mention = candidate
                    parsed_destination = candidate.reference
                elif (
                    prior_destination is not None
                    and prior_destination.kind is LocationKind.COUNTRY
                    and candidate.reference.country_code == prior_destination.country_code
                ):
                    # A country clarification offers supported cities/airports;
                    # an unmarked matching answer is therefore a destination.
                    parsed_destination_mention = candidate
                    parsed_destination = candidate.reference
                elif prior_destination is not None and prior_origin is None:
                    # After a destination question, an unmarked location is the
                    # next missing field: the departure airport.
                    parsed_origin_mention = candidate
                    parsed_origin = candidate.reference
                elif prior_destination is None and prior_origin is None:
                    # A bare supported location is a safe destination default;
                    # route language still takes precedence in _location_roles.
                    parsed_destination_mention = candidate
                    parsed_destination = candidate.reference
        confirmation_reference: LocationReference | None = None
        rejected_confirmation = False
        if action == "accept" and pending_confirmation is not None:
            confirmation_reference = pending_confirmation.reference
            pending_confirmation = None
            prior_destination = None
        elif action == "reject" and pending_confirmation is not None:
            pending_confirmation = None
            prior_destination = None
            rejected_confirmation = True
        elif parsed_destination_mention is not None and parsed_destination_mention.is_fuzzy:
            fuzzy_reference = self._canonical_reference(parsed_destination_mention.reference)
            if fuzzy_reference is not None and fuzzy_reference.kind is not LocationKind.UNKNOWN:
                pending_confirmation = PendingDestinationConfirmation(
                    original_text=parsed_destination_mention.matched_text,
                    reference=fuzzy_reference,
                )
            parsed_destination = None
        elif parsed_destination_mention is not None:
            # An exact new destination supersedes a stale pending suggestion.
            pending_confirmation = None
            pending_dynamic_choices = None
            destination_source = None
        elif pending_confirmation is not None:
            # Do not let an LLM-inferred destination bypass explicit confirmation.
            parsed_destination = None
        if parsed_origin_mention is not None and parsed_origin_mention.is_fuzzy:
            # There is no origin confirmation slot in this checkpoint contract;
            # fail closed and ask for a supported departure airport.
            parsed_origin = None
        elif parsed_origin_mention is not None:
            origin_source = None
        if confirmation_reference is not None:
            destination = self._canonical_reference(
                confirmation_reference,
                dynamic_source=destination_source,
            )
            pending_dynamic_choices = None
        elif rejected_confirmation or pending_confirmation is not None or dynamic_repeat:
            destination = None
        elif parsed_destination_mention is not None:
            destination = self._canonical_reference(parsed_destination)
        else:
            destination = self._canonical_reference(
                parsed_destination or command.destination or prior_destination,
                dynamic_source=destination_source,
            )
        if parsed_origin_mention is not None:
            origin = self._canonical_reference(parsed_origin)
        else:
            origin = self._canonical_reference(
                parsed_origin or command.origin or prior_origin,
                dynamic_source=origin_source,
            )
        if pending_dynamic_origin_choices is not None and (
            parsed_origin_mention is not None
            or semantic_origin is not None
            or command.origin is not None
        ):
            pending_dynamic_origin_choices = None
        if destination is None and not (
            pending_confirmation is not None
            or rejected_confirmation
            or pending_dynamic_choices is not None
            or dynamic_repeat
        ):
            destination = self._unknown_destination(message)

        if trusted_preferences is None and safe_preferences is not None:
            trusted_preferences = safe_preferences
        if preference_timezone is None and isinstance(trusted_preferences, Mapping):
            raw_timezone = trusted_preferences.get("timezone")
            if isinstance(raw_timezone, str) and raw_timezone.strip():
                preference_timezone = raw_timezone
        if preference_timezone is None and isinstance(safe_context, Mapping):
            preference_context = safe_context.get("travel_preferences_v1")
            if isinstance(preference_context, Mapping):
                raw_timezone = preference_context.get("timezone")
                if isinstance(raw_timezone, str) and raw_timezone.strip():
                    preference_timezone = raw_timezone
        date_resolution = self.date_resolution
        if preference_timezone and preference_timezone != self.date_resolution.timezone:
            date_resolution = DateResolutionService(
                clock=self.clock,
                timezone=preference_timezone,
            )

        date_error: DateResolutionError | None = None
        date_window = command.date_window
        if date_window is None and message.strip():
            try:
                parsed_dates = date_resolution.resolve(message, locale=locale)
            except DateResolutionError as exc:
                date_error = exc
                parsed_dates = None
            if parsed_dates is not None:
                date_window = parsed_dates
        if date_window is None:
            date_window = prior_dates
        if date_window is not None and date_window.start_date < date_resolution.local_today():
            date_error = date_error or PastDateError("requested date is before local today")
            if command.date_window is not None:
                date_window = None

        projection = self._projection(
            origin,
            destination,
            date_window,
            pending_confirmation,
            origin_resolution_source=origin_source,
            destination_resolution_source=destination_source,
            dynamic_destination_choices=pending_dynamic_choices,
            dynamic_origin_choices=pending_dynamic_origin_choices,
        )

        if pending_confirmation is not None:
            reference = pending_confirmation.reference
            display_name = reference.normalized_name
            choice_value = reference.city_id or (
                reference.airport_candidates[0]
                if reference.airport_candidates
                else reference.normalized_name
            )
            result = self._clarification(
                reason=ClarificationReason.POSSIBLE_DESTINATION_TYPO,
                missing_fields=("destination",),
                question_vi=(
                    f"Tôi hiểu “{pending_confirmation.original_text}” có thể là "
                    f"{display_name}. Bạn có muốn nói đến địa điểm này không? "
                    "Vui lòng xác nhận trước khi tôi tìm chuyến bay."
                ),
                question_en=(
                    f"Did you mean “{display_name}” for "
                    f"“{pending_confirmation.original_text}”? Please confirm before I search."
                ),
                choices=(
                    ClarificationChoice(
                        value=choice_value,
                        label_vi=display_name,
                        label_en=display_name,
                    ),
                ),
            )
            return result, projection

        if pending_dynamic_origin_choices is not None:
            choices = tuple(
                ClarificationChoice(
                    value=choice.value,
                    label_vi=choice.label_vi,
                    label_en=choice.label_en,
                )
                for choice in pending_dynamic_origin_choices.choices
            )
            result = self._clarification(
                reason=ClarificationReason.MISSING_ORIGIN,
                missing_fields=("origin",),
                question_vi="Vui lòng chọn một sân bay khởi hành cụ thể.",
                question_en="Please choose one specific departure airport.",
                choices=choices,
            )
            return result, projection

        if pending_dynamic_choices is not None or dynamic_repeat:
            choices = tuple(
                ClarificationChoice(
                    value=choice.value,
                    label_vi=choice.label_vi,
                    label_en=choice.label_en,
                )
                for choice in (
                    pending_dynamic_choices.choices if pending_dynamic_choices is not None else ()
                )
            )
            if choices:
                if dynamic_destination_interpretation in {"probable", "uncertain"}:
                    query = (
                        dynamic_destination_query
                        or (
                            pending_dynamic_choices.query_label if pending_dynamic_choices else None
                        )
                        or "that place"
                    )
                    question_vi = (
                        f"Nếu bạn muốn nói đến {query}, bạn muốn đến thành phố hoặc sân bay nào?"
                    )
                    question_en = f"If you mean {query}, which matching city or airport would you like to visit?"
                else:
                    query = (
                        dynamic_destination_query
                        or (
                            pending_dynamic_choices.query_label if pending_dynamic_choices else None
                        )
                        or "that destination"
                    )
                    question_vi = f"Bạn muốn đến thành phố hoặc sân bay phù hợp nào ở {query}?"
                    question_en = (
                        f"Which matching city or airport in {query} would you like to visit?"
                    )
                result = self._clarification(
                    reason=ClarificationReason.DYNAMIC_DESTINATION_CHOICES,
                    missing_fields=("destination",),
                    question_vi=question_vi,
                    question_en=question_en,
                    choices=choices,
                )
                return result, projection

        if destination is None:
            result = self._clarification(
                reason=ClarificationReason.MISSING_DESTINATION,
                missing_fields=("destination",),
                question_vi="Bạn muốn đến thành phố hoặc sân bay nào?",
                question_en="Which supported city or airport would you like to visit?",
            )
            return result, projection
        if destination.kind is LocationKind.UNKNOWN:
            result = self._clarification(
                reason=ClarificationReason.UNSUPPORTED_LOCATION,
                missing_fields=("destination",),
                question_vi="Tôi chưa hỗ trợ địa điểm này. Vui lòng chọn một thành phố hoặc sân bay được hỗ trợ.",
                question_en="I do not support that location yet. Please choose a supported city or airport.",
            )
            return result, projection
        if destination.kind is LocationKind.COUNTRY:
            result = self._clarification(
                reason=ClarificationReason.AMBIGUOUS_DESTINATION,
                missing_fields=("destination",),
                question_vi="Bạn muốn đến thành phố hoặc sân bay nào trong quốc gia này?",
                question_en="Which supported city or airport in that country would you like to visit?",
                choices=self._country_choices(destination),
            )
            return result, projection
        if not destination.airport_candidates:
            result = self._clarification(
                reason=ClarificationReason.UNSUPPORTED_LOCATION,
                missing_fields=("destination",),
                question_vi="Vui lòng chọn một thành phố hoặc sân bay có sân bay được hỗ trợ.",
                question_en="Please choose a supported city or airport with available airport candidates.",
            )
            return result, projection

        if default_origin_airport is not None:
            trusted_preferences = {
                **dict(trusted_preferences or {}),
                "default_origin_airport": default_origin_airport,
            }
        trusted_origin = self._origin_from_trusted_preferences(
            safe_context=safe_context,
            trusted_preferences=trusted_preferences,
        )
        if origin is None:
            origin = trusted_origin
            projection = self._projection(
                origin,
                destination,
                date_window,
                pending_confirmation,
                origin_resolution_source=origin_source,
                destination_resolution_source=destination_source,
                dynamic_destination_choices=pending_dynamic_choices,
                dynamic_origin_choices=pending_dynamic_origin_choices,
            )
        if origin is None:
            result = self._clarification(
                reason=ClarificationReason.MISSING_ORIGIN,
                missing_fields=("origin",),
                question_vi="Bạn sẽ khởi hành từ thành phố hoặc sân bay nào?",
                question_en="Which city or airport will you depart from?",
                choices=self._origin_choices(),
            )
            return result, projection
        if origin.kind is LocationKind.UNKNOWN or len(origin.airport_candidates) != 1:
            result = self._clarification(
                reason=ClarificationReason.MISSING_ORIGIN,
                missing_fields=("origin",),
                question_vi="Vui lòng chọn một sân bay khởi hành cụ thể.",
                question_en="Please choose one specific departure airport.",
                choices=self._origin_choices(),
            )
            return result, projection

        if date_error is not None:
            reason = self._date_error_reason(date_error)
            if reason is ClarificationReason.DATE_WINDOW_TOO_WIDE:
                question_vi = "Vui lòng thu hẹp khoảng ngày xuống tối đa bảy ngày."
                question_en = "Please narrow the travel window to seven days or fewer."
            elif reason is ClarificationReason.PAST_DATE:
                question_vi = "Ngày đi phải là hôm nay hoặc một ngày trong tương lai."
                question_en = "The departure date must be today or a future date."
            else:
                question_vi = "Vui lòng cho biết một ngày đi hoặc khoảng ngày hợp lệ."
                question_en = "Please provide one valid departure date or a flexible date range."
            result = self._clarification(
                reason=reason,
                missing_fields=("date_window",),
                question_vi=question_vi,
                question_en=question_en,
            )
            return result, projection
        if date_window is None:
            result = self._clarification(
                reason=ClarificationReason.MISSING_DATES,
                missing_fields=("date_window",),
                question_vi="Bạn muốn đi vào ngày nào hoặc trong khoảng ngày nào?",
                question_en="What travel date or date window would you like?",
            )
            return result, projection

        origin_code = origin.airport_candidates[0]
        if origin_code in destination.airport_candidates:
            result = self._clarification(
                reason=ClarificationReason.MISSING_ORIGIN,
                missing_fields=("origin",),
                question_vi="Vui lòng chọn sân bay khởi hành khác với điểm đến.",
                question_en="Please choose a departure airport different from the destination.",
                choices=self._origin_choices(),
            )
            return result, projection

        result = ExecutableFlightSearch(
            resolved_origin=origin_code,
            destination_airports=destination.airport_candidates,
            date_window=date_window,
            passengers=command.passengers,
            cabin=command.cabin,
            currency=command.currency,
            max_stops=command.max_stops,
            baggage_required=command.baggage_required,
            preferred_departure_start=command.preferred_departure_start,
            preferred_departure_end=command.preferred_departure_end,
        )
        return result, projection

    def resolve(
        self,
        command: TripDiscoveryCommand | str | None = None,
        message: str = "",
        *,
        safe_context: Mapping[str, Any] | None = None,
        trusted_preferences: Mapping[str, Any] | None = None,
        safe_preferences: Mapping[str, Any] | None = None,
        default_origin_airport: str | None = None,
        preference_timezone: str | None = None,
        locale: str = "en",
        interpreted_origin: LocationReference | None = None,
        interpreted_origin_source: Literal["catalog", "duffel", "fixture"] | None = None,
        interpreted_destination: LocationReference | None = None,
        interpreted_source_text: str | None = None,
        interpreted_destination_source: Literal["catalog", "duffel", "fixture"] | None = None,
        interpreted_destination_requires_confirmation: bool = False,
        dynamic_destination_choices: tuple[DynamicDestinationChoice, ...] | None = None,
        dynamic_destination_source: Literal["catalog", "duffel", "fixture"] | None = None,
        dynamic_destination_kind: Literal["airport", "city", "country", "region", "unknown"]
        | None = None,
        dynamic_destination_interpretation: Literal[
            "exact", "probable", "uncertain", "unknown"
        ] = "exact",
        dynamic_destination_query: str | None = None,
        confirmation_action_override: Literal["accept", "reject"] | None = None,
        dynamic_choice_action_override: Literal["accept_any_destination", "continue_pending"]
        | None = None,
    ) -> DiscoveryResult:
        return self.resolve_with_projection(
            command,
            message=message,
            safe_context=safe_context,
            trusted_preferences=trusted_preferences,
            safe_preferences=safe_preferences,
            default_origin_airport=default_origin_airport,
            preference_timezone=preference_timezone,
            locale=locale,
            interpreted_origin=interpreted_origin,
            interpreted_origin_source=interpreted_origin_source,
            interpreted_destination=interpreted_destination,
            interpreted_source_text=interpreted_source_text,
            interpreted_destination_source=interpreted_destination_source,
            interpreted_destination_requires_confirmation=interpreted_destination_requires_confirmation,
            dynamic_destination_choices=dynamic_destination_choices,
            dynamic_destination_source=dynamic_destination_source,
            dynamic_destination_kind=dynamic_destination_kind,
            dynamic_destination_interpretation=dynamic_destination_interpretation,
            dynamic_destination_query=dynamic_destination_query,
            confirmation_action_override=confirmation_action_override,
            dynamic_choice_action_override=dynamic_choice_action_override,
        )[0]

    @staticmethod
    def is_single_exact_search(result: DiscoveryResult) -> bool:
        return (
            isinstance(result, ExecutableFlightSearch)
            and len(result.destination_airports) == 1
            and result.date_window.start_date == result.date_window.end_date
        )


__all__ = ["TripDiscoveryService"]
