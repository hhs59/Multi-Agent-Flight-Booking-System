# ruff: noqa: ARG002

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from pydantic import SecretStr

from agent_system.domain.bookings import (
    BookingQuote,
    HoldReference,
    ProviderOrderReference,
    TravelerSnapshot,
)
from agent_system.domain.flights import (
    FlightOffer,
    FlightSearchCriteria,
    PassengerType,
    ProviderCapabilities,
    RepriceResult,
    RepriceStatus,
    SearchResultPage,
)
from agent_system.domain.limits import MAX_PROVIDER_OFFERS_PER_ATTEMPT
from agent_system.domain.values import ExecutionMode, ProviderMetadata
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.duffel.client import DuffelClient
from agent_system.providers.duffel.mapper import map_offer, map_search_page
from agent_system.providers.duffel.settings import DuffelSettings
from agent_system.providers.errors import (
    CapabilityUnavailable,
    OfferExpiredError,
    OfferUnavailableError,
    ProviderMalformedResponseError,
    ProviderValidationError,
)
from agent_system.providers.localization import AirportCatalog

logger = logging.getLogger(__name__)


class DuffelFlightProvider:
    name = "duffel"

    def __init__(
        self,
        settings: DuffelSettings,
        client: DuffelClient,
        *,
        clock: Clock | None = None,
        airports: AirportCatalog | None = None,
    ) -> None:
        self.settings = settings
        self.environment = settings.environment
        self.client = client
        self.clock = clock or SystemClock()
        self.airports = airports or AirportCatalog.from_package_data()

    def capabilities(self) -> ProviderCapabilities:
        can_book = self.settings.order_enabled and self.environment is ExecutionMode.SANDBOX
        return ProviderCapabilities(
            can_search=True,
            can_reprice=True,
            can_book=can_book,
            requires_instant_payment=can_book,
            can_cancel=False,
        )

    @staticmethod
    def _passengers(criteria: FlightSearchCriteria) -> list[dict[str, str]]:
        passengers = [{"type": "adult"}] * criteria.passengers.adults
        passengers.extend({"type": "child"} for _ in range(criteria.passengers.children))
        passengers.extend(
            {"type": "infant_without_seat"} for _ in range(criteria.passengers.infants)
        )
        return passengers

    @staticmethod
    def _carrier_codes(raw_offer: Mapping[str, Any]) -> set[str]:
        codes: set[str] = set()
        owner = raw_offer.get("owner")
        if isinstance(owner, Mapping) and owner.get("iata_code"):
            codes.add(str(owner["iata_code"]).strip().upper())
        raw_slices = raw_offer.get("slices")
        if not isinstance(raw_slices, list):
            return codes
        for raw_slice in raw_slices:
            if not isinstance(raw_slice, Mapping):
                continue
            raw_segments = raw_slice.get("segments")
            if not isinstance(raw_segments, list):
                continue
            for raw_segment in raw_segments:
                if not isinstance(raw_segment, Mapping):
                    continue
                for key in ("marketing_carrier", "operating_carrier"):
                    carrier = raw_segment.get(key)
                    if isinstance(carrier, Mapping) and carrier.get("iata_code"):
                        codes.add(str(carrier["iata_code"]).strip().upper())
        return codes

    @classmethod
    def _matches_preferred_carriers(
        cls,
        raw_offer: Mapping[str, Any],
        preferred_carriers: tuple[str, ...],
    ) -> bool:
        return bool(cls._carrier_codes(raw_offer) & set(preferred_carriers))

    @staticmethod
    def _search_body(criteria: FlightSearchCriteria) -> dict[str, Any]:
        slices: list[dict[str, str]] = [
            {
                "origin": criteria.origin,
                "destination": criteria.destination,
                "departure_date": criteria.departure_date.isoformat(),
            }
        ]
        if criteria.return_date is not None:
            slices.append(
                {
                    "origin": criteria.destination,
                    "destination": criteria.origin,
                    "departure_date": criteria.return_date.isoformat(),
                }
            )
        body: dict[str, Any] = {
            "slices": slices,
            "passengers": DuffelFlightProvider._passengers(criteria),
            "cabin_class": criteria.cabin.value,
            "currency": criteria.currency,
        }
        # Leave this filter out when the user did not request a stop limit. Duffel
        # applies its documented default in that case; an arbitrary provider-side
        # limit would turn an unconstrained search into a rejected request.
        if criteria.max_stops is not None:
            body["max_connections"] = criteria.max_stops
        return body

    async def search(
        self,
        criteria: FlightSearchCriteria,
        *,
        correlation_id: str | None = None,
    ) -> SearchResultPage:
        payload = await self.client.request_json(
            "POST",
            "/air/offer_requests",
            params={"return_offers": True},
            json_body={"data": self._search_body(criteria)},
            operation="search",
            correlation_id=correlation_id,
        )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation="search",
                safe_message="provider search response has no offer request",
            )
        raw_offers = data.get("offers")
        if not isinstance(raw_offers, list):
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation="search",
                safe_message="provider search response has no offers",
            )
        filtered_offers = raw_offers
        if criteria.preferred_carriers:
            filtered_offers = [
                raw_offer
                for raw_offer in raw_offers
                if isinstance(raw_offer, Mapping)
                and self._matches_preferred_carriers(raw_offer, criteria.preferred_carriers)
            ]
        if len(filtered_offers) > MAX_PROVIDER_OFFERS_PER_ATTEMPT:
            logger.info(
                "duffel_search_truncation_metric",
                extra={
                    "metric_name": "provider_search_truncations_total",
                    "provider": self.name,
                    "environment": self.environment.value,
                },
            )
        bounded_data = dict(data)
        bounded_data["offers"] = list(filtered_offers[:MAX_PROVIDER_OFFERS_PER_ATTEMPT])
        bounded_payload = dict(payload)
        bounded_payload["data"] = bounded_data
        retrieved_at = self.clock.now()
        page = map_search_page(
            bounded_payload,
            criteria=criteria,
            environment=self.environment,
            retrieved_at=retrieved_at,
            offer_ttl=self.settings.offer_ttl,
            correlation_id=correlation_id,
            airports=self.airports,
        )
        warnings = list(page.warnings)
        if criteria.preferred_carriers:
            warnings.append("Preferred-carrier filtering was applied after provider search.")
        if len(filtered_offers) > MAX_PROVIDER_OFFERS_PER_ATTEMPT:
            warnings.append("Provider search results were limited to the safe offer bound.")
        page.warnings = tuple(warnings)
        return page

    async def reprice(
        self,
        provider_offer_id: str,
        expected: FlightOffer,
        *,
        correlation_id: str | None = None,
    ) -> RepriceResult:
        if (
            expected.metadata.provider != self.name
            or expected.metadata.environment is not self.environment
        ):
            raise ProviderValidationError(
                provider=self.name,
                operation="reprice",
                safe_message="offer provenance does not match selected provider",
            )
        if expected.metadata.provider_offer_id != provider_offer_id:
            raise ProviderValidationError(
                provider=self.name,
                operation="reprice",
                safe_message="provider offer ID does not match expected offer",
            )
        now = self.clock.now()
        metadata = ProviderMetadata(
            provider=self.name,
            environment=self.environment,
            is_live=self.environment is ExecutionMode.PRODUCTION,
            retrieved_at=now,
            expires_at=now + self.settings.offer_ttl,
            provider_offer_id=provider_offer_id,
            correlation_id=correlation_id,
        )
        if expected.is_expired(now):
            return RepriceResult(
                metadata=metadata,
                original_offer_id=expected.id,
                status=RepriceStatus.EXPIRED,
                reason="offer expired; search again",
            )
        try:
            payload = await self.client.request_json(
                "GET",
                f"/air/offers/{quote(provider_offer_id, safe='')}",
                params={"return_available_services": False},
                operation="reprice",
                correlation_id=correlation_id,
            )
        except OfferExpiredError:
            return RepriceResult(
                metadata=metadata,
                original_offer_id=expected.id,
                status=RepriceStatus.EXPIRED,
                reason="offer expired; search again",
            )
        except OfferUnavailableError:
            return RepriceResult(
                metadata=metadata,
                original_offer_id=expected.id,
                status=RepriceStatus.UNAVAILABLE,
                reason="flight offer is no longer available",
            )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation="reprice",
                safe_message="provider pricing response has no offer",
            )
        mapped = map_offer(
            data,
            environment=self.environment,
            retrieved_at=now,
            offer_ttl=self.settings.offer_ttl,
            correlation_id=correlation_id,
            airports=self.airports,
        )
        if mapped.metadata.provider_offer_id != provider_offer_id:
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation="reprice",
                safe_message="provider pricing response changed offer identity",
            )
        return RepriceResult(
            metadata=mapped.metadata,
            original_offer_id=expected.id,
            status=RepriceStatus.UNCHANGED
            if mapped.total == expected.total
            else RepriceStatus.CHANGED,
            repriced_offer=mapped,
        )

    def _unsupported(self, operation: str) -> None:
        raise CapabilityUnavailable(
            provider=self.name,
            operation=operation,
            safe_message="Duffel order capability is disabled",
        )

    @staticmethod
    def _duffel_title(value: str | None) -> str:
        normalized = value.strip().lower() if value else ""
        if normalized not in {"mr", "mrs", "ms", "miss", "dr"}:
            raise ProviderValidationError(
                provider="duffel",
                operation="create_order",
                safe_message="traveler title is missing or unsupported",
            )
        return normalized

    @staticmethod
    def _duffel_gender(value: str | None) -> str:
        normalized = value.strip().lower() if value else ""
        mapped = {"m": "m", "male": "m", "f": "f", "female": "f"}.get(normalized)
        if mapped is None:
            raise ProviderValidationError(
                provider="duffel",
                operation="create_order",
                safe_message="traveler gender marker is missing or unsupported",
            )
        return mapped

    def _order_payload(
        self,
        quote: BookingQuote,
        travelers: tuple[TravelerSnapshot, ...],
    ) -> dict[str, Any]:
        if not self.settings.order_enabled or self.environment is not ExecutionMode.SANDBOX:
            self._unsupported("create_order")
        offer = quote.offer
        now = self.clock.now()
        if quote.expires_at <= now or offer.is_expired(now):
            raise OfferExpiredError(
                provider=self.name,
                operation="create_order",
                safe_message="booking quote has expired",
            )
        if (
            offer.metadata.provider != self.name
            or offer.metadata.environment is not self.environment
        ):
            raise ProviderValidationError(
                provider=self.name,
                operation="create_order",
                safe_message="booking quote provider environment is not Duffel sandbox",
            )
        provider_offer_id = offer.metadata.provider_offer_id
        if not provider_offer_id:
            raise ProviderValidationError(
                provider=self.name,
                operation="create_order",
                safe_message="booking quote has no provider offer ID",
            )
        references = offer.provider_passengers
        if len(references) != len(travelers):
            raise ProviderValidationError(
                provider=self.name,
                operation="create_order",
                safe_message="provider passenger count does not match travelers",
            )
        if any(reference.passenger_type is not PassengerType.ADULT for reference in references):
            raise ProviderValidationError(
                provider=self.name,
                operation="create_order",
                safe_message="children and infants are not supported for Duffel orders yet",
            )
        passengers: list[dict[str, Any]] = []
        for reference, traveler in zip(references, travelers, strict=True):
            if not all(
                (
                    traveler.title,
                    traveler.given_name,
                    traveler.family_name,
                    traveler.gender_marker,
                    traveler.email,
                    traveler.phone,
                )
            ):
                raise ProviderValidationError(
                    provider=self.name,
                    operation="create_order",
                    safe_message="traveler is missing Duffel-required structured fields",
                )
            passenger: dict[str, Any] = {
                "id": reference.provider_passenger_id,
                "title": self._duffel_title(traveler.title),
                "given_name": traveler.given_name,
                "family_name": traveler.family_name,
                "born_on": traveler.birth_date.isoformat(),
                "gender": self._duffel_gender(traveler.gender_marker),
                "email": traveler.email,
                "phone_number": traveler.phone,
            }
            passport_values = (
                traveler.passport_number,
                traveler.passport_issuing_country,
                traveler.passport_expiry_date,
            )
            if any(value is not None for value in passport_values):
                if not all(value is not None for value in passport_values):
                    raise ProviderValidationError(
                        provider=self.name,
                        operation="create_order",
                        safe_message="passport identity fields must be complete",
                    )
                passenger["identity_documents"] = [
                    {
                        "type": "passport",
                        "unique_identifier": traveler.passport_number.get_secret_value(),
                        "issuing_country_code": traveler.passport_issuing_country,
                        "expires_on": traveler.passport_expiry_date.isoformat(),
                    }
                ]
            passengers.append(passenger)
        return {
            "data": {
                "type": "instant",
                "selected_offers": [provider_offer_id],
                "payments": [
                    {
                        "type": self.settings.settlement_mode,
                        "currency": offer.total.currency,
                        "amount": str(offer.total.amount),
                    }
                ],
                "passengers": passengers,
            }
        }

    def _map_order(
        self,
        payload: Mapping[str, Any],
        *,
        operation: str,
        status_code: int,
    ) -> ProviderOrderReference:
        if status_code not in {200, 201}:
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation=operation,
                safe_message="provider returned an asynchronous order response",
                http_status=status_code,
            )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation=operation,
                safe_message="provider order response has no order data",
            )
        order_id = data.get("id")
        live_mode = data.get("live_mode")
        if not isinstance(order_id, str) or not order_id.strip():
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation=operation,
                safe_message="provider order response has no order ID",
            )
        if not isinstance(live_mode, bool) or live_mode:
            raise ProviderValidationError(
                provider=self.name,
                operation=operation,
                safe_message="provider returned a live order in sandbox mode",
            )
        booking_reference = data.get("booking_reference")
        if booking_reference is not None and not isinstance(booking_reference, str):
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation=operation,
                safe_message="provider order response has an invalid booking reference",
            )
        provider_status = data.get("status")
        if provider_status is not None and not isinstance(provider_status, str):
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation=operation,
                safe_message="provider order response has an invalid status",
            )
        now = self.clock.now()
        return ProviderOrderReference(
            metadata=ProviderMetadata(
                provider=self.name,
                environment=self.environment,
                is_live=False,
                retrieved_at=now,
            ),
            provider_order_id=SecretStr(order_id.strip()),
            booking_reference=booking_reference.strip() if booking_reference else None,
            provider_status=provider_status.strip() if provider_status else None,
            live_mode=live_mode,
        )

    async def create_order(
        self,
        quote: BookingQuote,
        travelers: tuple[TravelerSnapshot, ...],
        idempotency_key: str,
    ) -> ProviderOrderReference:
        payload = self._order_payload(quote, travelers)
        response = await self.client.request_envelope(
            "POST",
            "/air/orders",
            json_body=payload,
            operation="create_order",
            idempotency_key=idempotency_key,
            timeout_seconds=self.settings.order_timeout_seconds,
        )
        return self._map_order(
            response.payload,
            operation="create_order",
            status_code=response.status_code,
        )

    async def get_order(self, provider_order_id: str) -> ProviderOrderReference:
        if not self.settings.order_enabled or self.environment is not ExecutionMode.SANDBOX:
            self._unsupported("get_order")
        if not provider_order_id.strip():
            raise ProviderValidationError(
                provider=self.name,
                operation="get_order",
                safe_message="provider order ID is required",
            )
        response = await self.client.request_envelope(
            "GET",
            f"/air/orders/{quote(provider_order_id.strip(), safe='')}",
            operation="get_order",
        )
        return self._map_order(
            response.payload,
            operation="get_order",
            status_code=response.status_code,
        )

    async def cancel_order(
        self,
        provider_order_id: str,
        idempotency_key: str,
    ) -> ProviderOrderReference:
        self._unsupported("cancel_order")

    async def create_hold(
        self,
        quote: BookingQuote,
        travelers: tuple[TravelerSnapshot, ...],
        idempotency_key: str,
    ) -> HoldReference:
        self._unsupported("create_hold")

    async def release_hold(
        self,
        provider_hold_id: str,
        idempotency_key: str,
    ) -> None:
        self._unsupported("release_hold")
