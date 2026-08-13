from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    ProviderPassengerReference,
    SearchResultPage,
)
from agent_system.domain.limits import MAX_PROVIDER_OFFERS_PER_ATTEMPT
from agent_system.domain.values import ExecutionMode, Money, ProviderMetadata
from agent_system.providers.errors import ProviderMalformedResponseError
from agent_system.providers.localization import AirportCatalog

_CABINS = {
    "economy": CabinClass.ECONOMY,
    "premium_economy": CabinClass.PREMIUM_ECONOMY,
    "business": CabinClass.BUSINESS,
    "first": CabinClass.FIRST,
}
_PASSENGERS = {
    "adult": PassengerType.ADULT,
    "young_adult": PassengerType.ADULT,
    "child": PassengerType.CHILD,
    "infant": PassengerType.INFANT,
    "infant_without_seat": PassengerType.INFANT,
}


def _malformed(message: str) -> NoReturn:
    raise ProviderMalformedResponseError(
        provider="duffel",
        operation="map_response",
        safe_message=message,
    )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _malformed(f"provider response has invalid {label}")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, list):
        _malformed(f"provider response has invalid {label}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _malformed(f"provider response has invalid {label}")
    return value.strip()


def _amount(value: Any, label: str) -> Decimal:
    if isinstance(value, float) or not isinstance(value, (str, int, Decimal)):
        _malformed(f"provider response has invalid {label}")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ProviderMalformedResponseError(
            provider="duffel",
            operation="map_response",
            safe_message=f"provider response has invalid {label}",
        ) from exc
    if not amount.is_finite() or amount < 0:
        _malformed(f"provider response has invalid {label}")
    return amount


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        _malformed(f"provider response has invalid {label}")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ProviderMalformedResponseError(
            provider="duffel",
            operation="map_response",
            safe_message=f"provider response has invalid {label}",
        ) from exc
    if parsed < 0:
        _malformed(f"provider response has invalid {label}")
    return parsed


def _parse_instant(
    value: Any,
    *,
    airport: Mapping[str, Any] | None = None,
    code: str = "",
    airports: AirportCatalog | None = None,
) -> datetime:
    raw = _text(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            timezone_name = airport.get("time_zone") if airport is not None else None
            if not isinstance(timezone_name, str) or not timezone_name.strip():
                if airports is None:
                    _malformed(f"provider response has no timezone for {code or 'timestamp'}")
                try:
                    timezone_name = airports.get(code).timezone
                except ValueError as exc:
                    raise ProviderMalformedResponseError(
                        provider="duffel",
                        operation="map_response",
                        safe_message="provider response has no timezone for airport",
                    ) from exc
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        return parsed.astimezone(UTC)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ProviderMalformedResponseError(
            provider="duffel",
            operation="map_response",
            safe_message="provider response has invalid timestamp",
        ) from exc


def _provider_expiry(
    payload: Mapping[str, Any],
    *,
    retrieved_at: datetime,
    offer_ttl: timedelta,
) -> datetime:
    raw = payload.get("expires_at")
    expiry = retrieved_at + offer_ttl if raw is None else _parse_instant(raw)
    if expiry <= retrieved_at:
        _malformed("provider response contains an expired offer")
    return expiry


def _check_live_mode(payload: Mapping[str, Any], environment: ExecutionMode) -> None:
    live_mode = payload.get("live_mode")
    if isinstance(live_mode, bool) and live_mode != (environment is ExecutionMode.PRODUCTION):
        _malformed("provider response live mode does not match configured environment")


def _map_segment(payload: Mapping[str, Any], *, airports: AirportCatalog) -> FlightSegment:
    origin = _mapping(payload.get("origin"), "segment origin")
    destination = _mapping(payload.get("destination"), "segment destination")
    origin_code = _text(origin.get("iata_code"), "segment origin airport")
    destination_code = _text(destination.get("iata_code"), "segment destination airport")
    marketing_payload = _mapping(payload.get("marketing_carrier"), "marketing carrier")
    operating_payload = payload.get("operating_carrier")
    operating = (
        _mapping(operating_payload, "operating carrier")
        if operating_payload is not None
        else marketing_payload
    )
    marketing = _text(marketing_payload.get("iata_code"), "marketing carrier code")
    operating_code = _text(operating.get("iata_code"), "operating carrier code")
    flight_number = payload.get("marketing_carrier_flight_number")
    if flight_number is None:
        flight_number = payload.get("operating_carrier_flight_number")
    return FlightSegment(
        origin=origin_code,
        destination=destination_code,
        departure_at=_parse_instant(
            payload.get("departing_at"),
            airport=origin,
            code=origin_code,
            airports=airports,
        ),
        arrival_at=_parse_instant(
            payload.get("arriving_at"),
            airport=destination,
            code=destination_code,
            airports=airports,
        ),
        marketing_carrier=marketing,
        operating_carrier=operating_code,
        flight_number=f"{marketing}{_text(flight_number, 'flight number')}",
        aircraft_code=(
            _text(
                _mapping(payload.get("aircraft"), "aircraft").get("iata_code"),
                "aircraft code",
            )
            if payload.get("aircraft") is not None
            and _mapping(payload.get("aircraft"), "aircraft").get("iata_code") is not None
            else None
        ),
    )


def _segment_passengers(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("passengers")
    if raw is None:
        return []
    return [_mapping(item, "segment passenger") for item in _sequence(raw, "segment passengers")]


def _cabin_and_fare(
    payload: Mapping[str, Any],
    segment_passengers: Sequence[Mapping[str, Any]],
) -> tuple[CabinClass, str | None, str | None]:
    cabins: set[CabinClass] = set()
    fare_brands: list[str] = []
    fare_bases: list[str] = []
    for passenger in segment_passengers:
        raw_cabin = passenger.get("cabin_class")
        if raw_cabin is not None:
            cabin = _CABINS.get(_text(raw_cabin, "cabin class").lower())
            if cabin is None:
                _malformed("provider response has unsupported cabin class")
            cabins.add(cabin)
        brand = passenger.get("cabin_class_marketing_name")
        if brand:
            fare_brands.append(_text(brand, "fare brand"))
        nested_cabin = passenger.get("cabin")
        if isinstance(nested_cabin, Mapping) and nested_cabin.get("marketing_name"):
            fare_brands.append(_text(nested_cabin.get("marketing_name"), "fare brand"))
        if passenger.get("fare_basis_code"):
            fare_bases.append(_text(passenger.get("fare_basis_code"), "fare basis"))
    if not cabins and payload.get("cabin_class") is not None:
        cabin = _CABINS.get(_text(payload.get("cabin_class"), "cabin class").lower())
        if cabin is None:
            _malformed("provider response has unsupported cabin class")
        cabins.add(cabin)
    if len(cabins) != 1:
        _malformed("provider response has missing or inconsistent cabin class")
    return (
        cabins.pop(),
        fare_brands[0] if fare_brands else None,
        fare_bases[0] if fare_bases else None,
    )


def _baggage(segment_passengers: Sequence[Mapping[str, Any]]) -> BaggageAllowance:
    checked_pieces: list[int] = []
    checked_weights: list[Decimal] = []
    cabin_pieces: list[int] = []
    personal_item = False
    for passenger in segment_passengers:
        raw_bags = passenger.get("baggages")
        if raw_bags is None:
            continue
        for raw_bag in _sequence(raw_bags, "baggage allowances"):
            bag = _mapping(raw_bag, "baggage allowance")
            kind = _text(bag.get("type"), "baggage type").lower()
            if kind in {"personal_item", "personal-item"}:
                personal_item = True
            quantity = bag.get("quantity")
            if quantity is not None:
                parsed_quantity = _integer(quantity, "baggage quantity")
                if kind == "checked":
                    checked_pieces.append(parsed_quantity)
                elif kind in {"carry_on", "carry-on", "cabin"}:
                    cabin_pieces.append(parsed_quantity)
            raw_weight = bag.get("weight_kg", bag.get("weight"))
            if raw_weight is not None and kind == "checked":
                unit = str(bag.get("weight_unit", bag.get("unit", "KG"))).upper()
                if unit == "KG":
                    checked_weights.append(_amount(raw_weight, "checked baggage weight"))
    return BaggageAllowance(
        checked_pieces=min(checked_pieces) if checked_pieces else None,
        checked_weight_kg=min(checked_weights) if checked_weights else None,
        cabin_pieces=min(cabin_pieces) if cabin_pieces else None,
        personal_item_included=personal_item if personal_item else None,
    )


def _passenger_counts(payload: Mapping[str, Any]) -> dict[PassengerType, int]:
    raw_passengers = payload.get("passengers")
    if raw_passengers is None:
        return {PassengerType.ADULT: 1}
    passengers = _sequence(raw_passengers, "offer passengers")
    if not passengers:
        _malformed("provider response has no offer passengers")
    counts: dict[PassengerType, int] = defaultdict(int)
    for raw_passenger in passengers:
        passenger = _mapping(raw_passenger, "offer passenger")
        raw_type = _text(passenger.get("type"), "passenger type").lower()
        passenger_type = _PASSENGERS.get(raw_type)
        if passenger_type is None:
            _malformed("provider response has unsupported passenger type")
        counts[passenger_type] += 1
    return dict(counts)


def _provider_passenger_references(
    payload: Mapping[str, Any],
) -> tuple[ProviderPassengerReference, ...]:
    raw_passengers = payload.get("passengers")
    if raw_passengers is None:
        return ()
    references: list[ProviderPassengerReference] = []
    for raw_passenger in _sequence(raw_passengers, "offer passengers"):
        passenger = _mapping(raw_passenger, "offer passenger")
        raw_id = passenger.get("id")
        raw_type = _text(passenger.get("type"), "passenger type").lower()
        passenger_type = _PASSENGERS.get(raw_type)
        if passenger_type is None:
            _malformed("provider response has unsupported passenger type")
        if isinstance(raw_id, str) and raw_id.strip():
            references.append(
                ProviderPassengerReference(
                    provider_passenger_id=raw_id.strip(),
                    passenger_type=passenger_type,
                )
            )
    return tuple(references)


def _allocate(amount: Decimal, counts: Mapping[PassengerType, int]) -> dict[PassengerType, Decimal]:
    total_quantity = sum(counts.values())
    if total_quantity < 1:
        _malformed("provider response has no passengers")
    allocations: dict[PassengerType, Decimal] = {}
    remaining = amount
    ordered = [
        item
        for item in (PassengerType.ADULT, PassengerType.CHILD, PassengerType.INFANT)
        if item in counts
    ]
    for index, passenger_type in enumerate(ordered):
        if index == len(ordered) - 1:
            allocation = remaining
        else:
            allocation = amount * Decimal(counts[passenger_type]) / Decimal(total_quantity)
            remaining -= allocation
        allocations[passenger_type] = allocation
    return allocations


def _passenger_prices(
    payload: Mapping[str, Any],
    *,
    currency: str,
    total: Decimal,
    base: Decimal,
) -> tuple[PassengerPrice, ...]:
    tax = total - base
    base_allocations = _allocate(base, _passenger_counts(payload))
    tax_allocations = _allocate(tax, _passenger_counts(payload))
    prices = []
    for passenger_type in (PassengerType.ADULT, PassengerType.CHILD, PassengerType.INFANT):
        quantity = _passenger_counts(payload).get(passenger_type)
        if quantity is None:
            continue
        base_amount = base_allocations[passenger_type]
        tax_amount = tax_allocations[passenger_type]
        prices.append(
            PassengerPrice(
                passenger_type=passenger_type,
                quantity=quantity,
                base=Money(amount=base_amount, currency=currency),
                taxes_and_fees=Money(amount=tax_amount, currency=currency),
                total=Money(amount=base_amount + tax_amount, currency=currency),
            )
        )
    return tuple(prices)


def _fare_conditions(payload: Mapping[str, Any], *, default_currency: str) -> FareConditions:
    raw_conditions = payload.get("conditions")
    conditions = raw_conditions if isinstance(raw_conditions, Mapping) else {}
    description_parts: list[str] = []
    values: dict[str, Any] = {}
    for key, field, label in (
        ("change_before_departure", "change", "change before departure"),
        ("refund_before_departure", "refund", "refund before departure"),
    ):
        raw_rule = conditions.get(key)
        if not isinstance(raw_rule, Mapping):
            continue
        allowed = raw_rule.get("allowed")
        if not isinstance(allowed, bool):
            _malformed(f"provider response has invalid {label} condition")
        amount = raw_rule.get("penalty_amount")
        fee = None
        if amount is not None:
            fee_currency = _text(
                raw_rule.get("penalty_currency", default_currency), f"{label} fee currency"
            )
            fee = Money(amount=_amount(amount, f"{label} fee"), currency=fee_currency)
        values[f"{field}_allowed"] = allowed
        values[f"{field}_fee"] = fee
        description_parts.append(f"{label}: {'allowed' if allowed else 'not allowed'}")
    return FareConditions(
        change_allowed=values.get("change_allowed"),
        change_fee=values.get("change_fee"),
        cancellation_allowed=values.get("refund_allowed"),
        cancellation_fee=values.get("refund_fee"),
        refundable=values.get("refund_allowed"),
        description="; ".join(description_parts) or None,
    )


def map_offer(
    payload: Mapping[str, Any],
    *,
    environment: ExecutionMode,
    retrieved_at: datetime,
    offer_ttl: timedelta,
    correlation_id: str | None,
    airports: AirportCatalog,
) -> FlightOffer:
    _check_live_mode(payload, environment)
    provider_offer_id = _text(payload.get("id"), "offer ID")
    currency = _text(payload.get("total_currency"), "offer currency")
    total = _amount(payload.get("total_amount"), "offer total")
    raw_base = payload.get("base_amount")
    base = _amount(total if raw_base is None else raw_base, "offer base")
    if (
        payload.get("base_currency") is not None
        and _text(payload.get("base_currency"), "base currency") != currency
    ):
        _malformed("provider response has inconsistent currencies")
    raw_tax = payload.get("tax_amount")
    tax = _amount(total - base if raw_tax is None else raw_tax, "offer tax")
    if (
        payload.get("tax_currency") is not None
        and _text(payload.get("tax_currency"), "tax currency") != currency
    ):
        _malformed("provider response has inconsistent currencies")
    if base + tax != total:
        _malformed("provider offer base and tax do not equal offer total")

    raw_slices = _sequence(payload.get("slices"), "slices")
    segments: list[FlightSegment] = []
    segment_passengers: list[Mapping[str, Any]] = []
    for raw_slice in raw_slices:
        slice_payload = _mapping(raw_slice, "slice")
        for raw_segment in _sequence(slice_payload.get("segments"), "segments"):
            segment_payload = _mapping(raw_segment, "segment")
            segments.append(_map_segment(segment_payload, airports=airports))
            segment_passengers.extend(_segment_passengers(segment_payload))
    if not segments:
        _malformed("provider response has no flight segments")

    owner = payload.get("owner")
    owner_code = (
        _text(_mapping(owner, "offer owner").get("iata_code"), "validating carrier")
        if owner is not None
        else segments[0].marketing_carrier
    )
    cabin, fare_brand, fare_basis = _cabin_and_fare(payload, segment_passengers)
    if fare_brand is None and payload.get("fare_brand_name"):
        fare_brand = _text(payload.get("fare_brand_name"), "fare brand")
    fare_conditions = _fare_conditions(payload, default_currency=currency)
    seats = payload.get("available_seats", payload.get("number_of_bookable_seats"))
    parsed_seats = None
    if seats is not None:
        parsed_seats = _integer(seats, "available seats")
        if parsed_seats > 99:
            _malformed("provider response has invalid available seats")
    now = retrieved_at.astimezone(UTC)
    expiry = _provider_expiry(payload, retrieved_at=now, offer_ttl=offer_ttl)
    return FlightOffer(
        metadata=ProviderMetadata(
            provider="duffel",
            environment=environment,
            is_live=environment is ExecutionMode.PRODUCTION,
            retrieved_at=now,
            expires_at=expiry,
            provider_offer_id=provider_offer_id,
            correlation_id=correlation_id,
        ),
        segments=tuple(segments),
        validating_carrier=owner_code,
        cabin=cabin,
        fare_brand=fare_brand,
        total=Money(amount=total, currency=currency),
        passenger_pricing=_passenger_prices(
            payload,
            currency=currency,
            total=total,
            base=base,
        ),
        baggage=_baggage(segment_passengers),
        fare_conditions=FareConditions(
            fare_basis=fare_basis,
            change_allowed=fare_conditions.change_allowed,
            change_fee=fare_conditions.change_fee,
            cancellation_allowed=fare_conditions.cancellation_allowed,
            cancellation_fee=fare_conditions.cancellation_fee,
            refundable=fare_conditions.refundable,
            description=fare_conditions.description,
        ),
        seats_available=parsed_seats,
        capabilities=ProviderCapabilities(can_search=True, can_reprice=True),
        provider_passengers=_provider_passenger_references(payload),
    )


def map_search_page(
    payload: Mapping[str, Any],
    *,
    criteria: FlightSearchCriteria,
    environment: ExecutionMode,
    retrieved_at: datetime,
    offer_ttl: timedelta,
    correlation_id: str | None,
    airports: AirportCatalog,
) -> SearchResultPage:
    data = _mapping(payload.get("data"), "offer request")
    _check_live_mode(data, environment)
    raw_offers = _sequence(data.get("offers"), "offer request offers")
    reported_total = len(raw_offers)
    offers = tuple(
        map_offer(
            _mapping(raw_offer, "flight offer"),
            environment=environment,
            retrieved_at=retrieved_at,
            offer_ttl=offer_ttl,
            correlation_id=correlation_id,
            airports=airports,
        )
        for raw_offer in raw_offers[:MAX_PROVIDER_OFFERS_PER_ATTEMPT]
    )
    return SearchResultPage(
        metadata=ProviderMetadata(
            provider="duffel",
            environment=environment,
            is_live=environment is ExecutionMode.PRODUCTION,
            retrieved_at=retrieved_at,
            expires_at=retrieved_at + offer_ttl,
            correlation_id=correlation_id,
        ),
        criteria=criteria,
        offers=offers,
        total_results=reported_total,
    )
