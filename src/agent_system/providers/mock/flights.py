from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from importlib.resources import files
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from agent_system.domain.bookings import (
    BookingQuote,
    HoldReference,
    ProviderOrderReference,
    TravelerSnapshot,
)
from agent_system.domain.flights import (
    BaggageAllowance,
    CabinClass,
    FareConditions,
    FlightOffer,
    FlightSearchCriteria,
    FlightSegment,
    PassengerPrice,
    PassengerType,
    ProviderCapabilities,
    RepriceResult,
    RepriceStatus,
    SearchResultPage,
)
from agent_system.domain.values import ExecutionMode, Money, ProviderMetadata
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.errors import (
    CapabilityUnavailable,
    IdempotencyConflictError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderValidationError,
)
from agent_system.providers.localization import AirportCatalog
from agent_system.providers.mock.scenarios import MockScenario

_CABIN_MULTIPLIERS = {
    CabinClass.ECONOMY: Decimal("1"),
    CabinClass.PREMIUM_ECONOMY: Decimal("1.45"),
    CabinClass.BUSINESS: Decimal("2.2"),
    CabinClass.FIRST: Decimal("3.5"),
}


class MockFlightProvider:
    name = "mock"
    environment = ExecutionMode.MOCK

    def __init__(
        self,
        *,
        scenario: MockScenario = MockScenario.SUCCESS,
        clock: Clock | None = None,
        airports: AirportCatalog | None = None,
    ) -> None:
        self.scenario = scenario
        self.clock = clock or SystemClock()
        self.airports = airports or AirportCatalog.from_v2_package_data()
        resource = files("agent_system.providers.mock").joinpath("data/vn_flight_offers.v1.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1 or "SYNTHETIC" not in payload.get(
            "inventory_notice", ""
        ):
            raise ValueError("mock inventory must be versioned and clearly synthetic")
        self._fixtures = tuple(payload["offers"])
        self._offers: dict[str, FlightOffer] = {}
        self._holds: dict[str, tuple[str, HoldReference]] = {}
        self._orders: dict[str, tuple[str, ProviderOrderReference, str]] = {}

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            can_search=True,
            can_reprice=True,
            can_hold=self.scenario is MockScenario.HOLD_SUPPORTED,
            can_book=self.scenario is MockScenario.BOOKING_SUPPORTED,
            can_cancel=self.scenario is MockScenario.BOOKING_SUPPORTED,
            can_refund=False,
        )

    def _raise_configured_failure(self, operation: str) -> None:
        if self.scenario is MockScenario.TIMEOUT:
            raise ProviderTimeoutError(
                provider=self.name,
                operation=operation,
                safe_message="synthetic provider timeout",
            )
        if self.scenario is MockScenario.RATE_LIMIT:
            raise ProviderRateLimitError(
                provider=self.name,
                operation=operation,
                safe_message="synthetic provider rate limit",
                retry_after_seconds=1,
                http_status=429,
            )

    def _provider_offer_id(self, fixture: dict, criteria: FlightSearchCriteria) -> str:
        passengers = criteria.passengers
        return (
            f"{fixture['provider_offer_id']}:{criteria.departure_date.isoformat()}:"
            f"{passengers.adults}-{passengers.children}-{passengers.infants}:"
            f"{criteria.cabin.value}"
        )

    def _segment_templates(
        self,
        fixture: dict,
        criteria: FlightSearchCriteria,
    ) -> tuple[FlightSegment, ...]:
        outbound = []
        for segment in fixture["segments"]:
            departure_local = datetime.combine(
                criteria.departure_date,
                time.fromisoformat(segment["departure_local_time"]),
                tzinfo=ZoneInfo(self.airports.get(segment["origin"]).timezone),
            )
            arrival_local = departure_local + timedelta(minutes=int(segment["duration_minutes"]))
            outbound.append(
                FlightSegment(
                    origin=segment["origin"],
                    destination=segment["destination"],
                    departure_at=departure_local.astimezone(UTC),
                    arrival_at=arrival_local.astimezone(UTC),
                    marketing_carrier=segment["marketing_carrier"],
                    operating_carrier=segment["operating_carrier"],
                    flight_number=segment["flight_number"],
                    aircraft_code=segment["aircraft_code"],
                )
            )
        if (
            not outbound
            or outbound[0].origin != criteria.origin
            or outbound[-1].destination != criteria.destination
        ):
            raise ValueError("synthetic fixture segments do not match their route")
        if criteria.return_date is None:
            return tuple(outbound)
        carrier = fixture["carrier"]
        return_departure = datetime.combine(
            criteria.return_date,
            time(18, 0),
            tzinfo=ZoneInfo(self.airports.get(criteria.destination).timezone),
        )
        return tuple(outbound) + (
            FlightSegment(
                origin=criteria.destination,
                destination=criteria.origin,
                departure_at=return_departure.astimezone(UTC),
                arrival_at=(
                    return_departure + timedelta(minutes=int(fixture["duration_minutes"]))
                ).astimezone(UTC),
                marketing_carrier=carrier,
                operating_carrier=carrier,
                flight_number=f"{carrier}997",
                aircraft_code="SYN",
            ),
        )

    def _pricing(
        self,
        fixture: dict,
        criteria: FlightSearchCriteria,
    ) -> tuple[tuple[PassengerPrice, ...], Money]:
        counts = (
            (PassengerType.ADULT, criteria.passengers.adults),
            (PassengerType.CHILD, criteria.passengers.children),
            (PassengerType.INFANT, criteria.passengers.infants),
        )
        cabin_multiplier = _CABIN_MULTIPLIERS[criteria.cabin]
        prices = []
        for passenger_type, quantity in counts:
            if quantity == 0:
                continue
            components = fixture["passenger_prices_vnd"][passenger_type.value]
            base_unit = (Decimal(components["base"]) * cabin_multiplier).quantize(Decimal("1"))
            total_unit = (Decimal(components["total"]) * cabin_multiplier).quantize(Decimal("1"))
            base_amount = base_unit * Decimal(quantity)
            total_amount = total_unit * Decimal(quantity)
            prices.append(
                PassengerPrice(
                    passenger_type=passenger_type,
                    quantity=quantity,
                    base=Money(amount=base_amount, currency="VND"),
                    taxes_and_fees=Money(
                        amount=total_amount - base_amount,
                        currency="VND",
                    ),
                    total=Money(amount=total_amount, currency="VND"),
                )
            )
        total = Money(
            amount=sum((price.total.amount for price in prices), Decimal("0")),
            currency="VND",
        )
        return tuple(prices), total

    def _build_offer(
        self,
        fixture: dict,
        criteria: FlightSearchCriteria,
        *,
        correlation_id: str | None,
    ) -> FlightOffer:
        now = self.clock.now()
        provider_offer_id = self._provider_offer_id(fixture, criteria)
        pricing, total = self._pricing(fixture, criteria)
        offer = FlightOffer(
            metadata=ProviderMetadata(
                provider=self.name,
                environment=self.environment,
                is_live=False,
                retrieved_at=now,
                expires_at=now + timedelta(minutes=15),
                provider_offer_id=provider_offer_id,
                correlation_id=correlation_id,
            ),
            segments=self._segment_templates(fixture, criteria),
            validating_carrier=fixture["carrier"],
            cabin=criteria.cabin,
            fare_brand=fixture["fare_brand"],
            total=total,
            passenger_pricing=pricing,
            baggage=BaggageAllowance(
                checked_pieces=fixture["baggage"]["checked_pieces"],
                checked_weight_kg=Decimal(fixture["baggage"]["checked_weight_kg"]),
                cabin_pieces=fixture["baggage"]["cabin_pieces"],
            ),
            fare_conditions=FareConditions(**fixture["fare_conditions"]),
            seats_available=int(fixture["seats"]),
            capabilities=self.capabilities(),
        )
        self._offers[provider_offer_id] = offer
        return offer

    async def search(
        self,
        criteria: FlightSearchCriteria,
        *,
        correlation_id: str | None = None,
    ) -> SearchResultPage:
        self._raise_configured_failure("search")
        if criteria.currency != "VND":
            raise ProviderValidationError(
                provider=self.name,
                operation="search",
                safe_message="synthetic Vietnam inventory supports only VND",
            )
        now = self.clock.now()
        if self.scenario is MockScenario.NO_RESULTS:
            return SearchResultPage(
                metadata=ProviderMetadata(
                    provider=self.name,
                    environment=self.environment,
                    is_live=False,
                    retrieved_at=now,
                    expires_at=now + timedelta(seconds=30),
                    correlation_id=correlation_id,
                ),
                criteria=criteria,
                offers=(),
                total_results=0,
            )
        route = f"{criteria.origin}-{criteria.destination}"
        fixtures = [
            fixture
            for fixture in self._fixtures
            if fixture["route"] == route
            and int(fixture["seats"]) >= criteria.passengers.total
            and criteria.cabin.value in fixture["supported_cabins"]
            and (criteria.max_stops is None or int(fixture["stops"]) <= criteria.max_stops)
            and (
                not criteria.preferred_carriers or fixture["carrier"] in criteria.preferred_carriers
            )
        ]
        offers = tuple(
            self._build_offer(fixture, criteria, correlation_id=correlation_id)
            for fixture in fixtures
        )
        return SearchResultPage(
            metadata=ProviderMetadata(
                provider=self.name,
                environment=self.environment,
                is_live=False,
                retrieved_at=now,
                expires_at=now + timedelta(minutes=15),
                correlation_id=correlation_id,
            ),
            criteria=criteria,
            offers=offers,
            total_results=len(offers),
            warnings=("SYNTHETIC TEST INVENTORY - NOT BOOKABLE",),
        )

    def _changed_offer(
        self,
        expected: FlightOffer,
        *,
        correlation_id: str | None,
    ) -> FlightOffer:
        changed_prices = []
        for price in expected.passenger_pricing:
            changed_total = (price.total.amount * Decimal("1.05")).quantize(Decimal("1"))
            changed_base = (price.base.amount * Decimal("1.05")).quantize(Decimal("1"))
            changed_prices.append(
                price.model_copy(
                    update={
                        "base": Money(amount=changed_base, currency=price.total.currency),
                        "taxes_and_fees": Money(
                            amount=changed_total - changed_base,
                            currency=price.total.currency,
                        ),
                        "total": Money(
                            amount=changed_total,
                            currency=price.total.currency,
                        ),
                    }
                )
            )
        now = self.clock.now()
        return expected.model_copy(
            update={
                "metadata": expected.metadata.model_copy(
                    update={
                        "retrieved_at": now,
                        "expires_at": now + timedelta(minutes=15),
                        "correlation_id": correlation_id,
                    }
                ),
                "passenger_pricing": tuple(changed_prices),
                "total": Money(
                    amount=sum(
                        (price.total.amount for price in changed_prices),
                        Decimal("0"),
                    ),
                    currency=expected.total.currency,
                ),
            }
        )

    async def reprice(
        self,
        provider_offer_id: str,
        expected: FlightOffer,
        *,
        correlation_id: str | None = None,
    ) -> RepriceResult:
        self._raise_configured_failure("reprice")
        if (
            expected.metadata.provider != self.name
            or expected.metadata.provider_offer_id != provider_offer_id
        ):
            raise ProviderValidationError(
                provider=self.name,
                operation="reprice",
                safe_message="offer provenance does not match selected provider",
            )
        now = self.clock.now()
        result_metadata = ProviderMetadata(
            provider=self.name,
            environment=self.environment,
            is_live=False,
            retrieved_at=now,
            expires_at=now + timedelta(minutes=5),
            correlation_id=correlation_id,
        )
        if self.scenario is MockScenario.EXPIRED_OFFER or expected.is_expired(now):
            return RepriceResult(
                metadata=result_metadata,
                original_offer_id=expected.id,
                status=RepriceStatus.EXPIRED,
                reason="synthetic offer expired",
            )
        if (
            self.scenario is MockScenario.UNAVAILABLE_SEGMENT
            or provider_offer_id not in self._offers
        ):
            return RepriceResult(
                metadata=result_metadata,
                original_offer_id=expected.id,
                status=RepriceStatus.UNAVAILABLE,
                reason="synthetic segment unavailable",
            )
        repriced = (
            self._changed_offer(expected, correlation_id=correlation_id)
            if self.scenario is MockScenario.PRICE_CHANGED
            else expected.model_copy(
                update={
                    "metadata": expected.metadata.model_copy(
                        update={
                            "retrieved_at": now,
                            "expires_at": now + timedelta(minutes=15),
                            "correlation_id": correlation_id,
                        }
                    )
                }
            )
        )
        return RepriceResult(
            metadata=result_metadata,
            original_offer_id=expected.id,
            status=(
                RepriceStatus.CHANGED
                if self.scenario is MockScenario.PRICE_CHANGED
                else RepriceStatus.UNCHANGED
            ),
            repriced_offer=repriced,
        )

    def _unsupported(self, operation: str) -> CapabilityUnavailable:
        return CapabilityUnavailable(
            provider=self.name,
            operation=operation,
            safe_message="operation is not implemented in Phase 3",
        )

    async def create_order(
        self,
        quote: BookingQuote,
        travelers: tuple[TravelerSnapshot, ...],
        idempotency_key: str,
    ) -> ProviderOrderReference:
        if self.scenario is not MockScenario.BOOKING_SUPPORTED:
            raise self._unsupported("create_order")
        self._raise_configured_failure("create_order")
        fingerprint = f"{quote.id}:{','.join(str(item.traveler_profile_id) for item in travelers)}"
        existing = self._orders.get(idempotency_key)
        if existing is not None:
            if existing[0] != fingerprint:
                raise IdempotencyConflictError(
                    provider=self.name,
                    operation="create_order",
                    safe_message="idempotency key was reused with different input",
                )
            return existing[1]
        now = self.clock.now()
        reference = ProviderOrderReference(
            metadata=ProviderMetadata(
                provider=self.name,
                environment=self.environment,
                is_live=False,
                retrieved_at=now,
                expires_at=None,
            ),
            provider_order_id=SecretStr(f"SYN-ORDER-{idempotency_key}"),
            booking_reference=f"SYN-{idempotency_key[-12:]}",
            provider_status="created",
            live_mode=False,
        )
        self._orders[idempotency_key] = (fingerprint, reference, "created")
        return reference

    async def get_order(self, provider_order_id: str) -> ProviderOrderReference:
        if self.scenario is not MockScenario.BOOKING_SUPPORTED:
            raise self._unsupported("get_order")
        for _, (_, reference, state) in self._orders.items():
            if (
                reference.provider_order_id.get_secret_value() == provider_order_id
                and state != "cancelled"
            ):
                return reference
        raise ProviderValidationError(
            provider=self.name, operation="get_order", safe_message="synthetic order was not found"
        )

    async def cancel_order(
        self, provider_order_id: str, idempotency_key: str
    ) -> ProviderOrderReference:
        del idempotency_key
        if self.scenario is not MockScenario.BOOKING_SUPPORTED:
            raise self._unsupported("cancel_order")
        for key, (fingerprint, reference, state) in self._orders.items():
            if reference.provider_order_id.get_secret_value() == provider_order_id:
                if state == "cancelled":
                    return reference
                self._orders[key] = (fingerprint, reference, "cancelled")
                return reference
        raise ProviderValidationError(
            provider=self.name,
            operation="cancel_order",
            safe_message="synthetic order was not found",
        )

    async def create_hold(
        self,
        quote: BookingQuote,
        travelers: tuple[TravelerSnapshot, ...],
        idempotency_key: str,
    ) -> HoldReference:
        if self.scenario is not MockScenario.HOLD_SUPPORTED:
            raise self._unsupported("create_hold")
        fingerprint = f"{quote.id}:{','.join(str(item.traveler_profile_id) for item in travelers)}"
        existing = self._holds.get(idempotency_key)
        if existing is not None:
            if existing[0] != fingerprint:
                raise IdempotencyConflictError(
                    provider=self.name,
                    operation="create_hold",
                    safe_message="idempotency key was reused with different input",
                )
            return existing[1]
        now = self.clock.now()
        hold = HoldReference(
            metadata=ProviderMetadata(
                provider=self.name,
                environment=self.environment,
                is_live=False,
                retrieved_at=now,
                expires_at=now + timedelta(minutes=15),
            ),
            provider_hold_id=SecretStr(f"SYN-HOLD-{idempotency_key}"),
            expires_at=now + timedelta(minutes=15),
        )
        self._holds[idempotency_key] = (fingerprint, hold)
        return hold

    async def release_hold(
        self,
        provider_hold_id: str,
        idempotency_key: str,
    ) -> None:
        del provider_hold_id, idempotency_key
        if self.scenario is not MockScenario.HOLD_SUPPORTED:
            raise self._unsupported("release_hold")
