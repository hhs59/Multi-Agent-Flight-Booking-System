import logging
import os
import re
from datetime import datetime, timedelta

import httpx
from cachetools import TTLCache

from agent_system.models import FlightResult

logger = logging.getLogger(__name__)

#Caches
_flights_cache: TTLCache = TTLCache(maxsize=256, ttl=6 * 3600)  # 6 hours
_weather_cache: TTLCache = TTLCache(maxsize=128, ttl=3600)  # 1 hour

_HTTP_TIMEOUT = httpx.Timeout(10.0)

#Airline Code -> Full Name
AIRLINE_CODES: dict[str, str] = {
    "VN": "Vietnam Airlines",
    "VJ": "VietJet Air",
    "NH": "ANA",
    "JL": "Japan Airlines",
    "SQ": "Singapore Airlines",
    "TG": "Thai Airways",
    "KE": "Korean Air",
    "OZ": "Asiana Airlines",
}

#IATA Code -> City name
IATA_CITY_NAMES: dict[str, str] = {
    "HAN": "Hanoi",
    "SGN": "Ho Chi Minh City",
    "DAD": "Da Nang",
    "CXR": "Nha Trang",
    "PQC": "Phu Quoc",
    "HPH": "Hai Phong",
    "HUI": "Hue",
    "VCA": "Can Tho",
    "DLI": "Da Lat",
    "NRT": "Tokyo",
    "SIN": "Singapore",
    "ICN": "Seoul",
    "BKK": "Bangkok",
}

def get_airline_name(code: str) -> str:
    return AIRLINE_CODES.get(code, code)


def get_iata_city_name(code: str) -> str:
    return f"{IATA_CITY_NAMES.get(code, code)} ({code})"


# Amadeus OAuth2
async def _get_amadeus_access_token() -> str | None:
    client_id = os.environ.get("AMADEUS_CLIENT_ID")
    client_secret = os.environ.get("AMADEUS_CLIENT_SECRET")

    if not client_id or not client_secret:
        logger.warning("Amadeus credentials not set — skipping token fetch")
        return None

    logger.info("Requesting Amadeus access token")
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                "https://test.api.amadeus.com/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
        if response.status_code == 200:
            token: str = response.json()["access_token"]
            logger.info("Amadeus token obtained")
            return token
        logger.warning("Amadeus token request returned %s", response.status_code)
        return None
    except httpx.HTTPError as exc:
        logger.warning("Amadeus token request failed: %s", exc)
        return None


def _parse_duration_minutes(duration: str) -> int:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", duration or "")
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    return hours * 60 + minutes


async def _parse_amadeus_offers(
    payload: dict,
    destination: str,
    date: str,
    mock: bool
    ) -> list[FlightResult]:
    weather = await get_weather(destination, date, mock=mock)
    results: list[FlightResult] = []

    for offer in payload.get("data", []):
        itineraries = offer.get("itineraries", [])
        if not itineraries:
            continue
        itinerary = itineraries[0]
        segments = itinerary.get("segments", [])
        if not segments:
            continue

        first_seg = segments[0]
        last_seg = segments[-1]
        carrier = last_seg.get("carrierCode") or first_seg.get("carrierCode", "")
        number = last_seg.get("number", "")
        flight_number = f"{carrier}{number}"

        try:
            departure_at = first_seg["departure"]["at"]
            arrival_at = last_seg["arrival"]["at"]
        except KeyError:
            logger.warning("Skipping Amadeus offer with malformed segments: %s", offer.get("id"))
            continue

        stops = max(len(segments) - 1, 0)
        duration_minutes = _parse_duration_minutes(itinerary.get("duration", ""))

        price = offer.get("price", {})
        price_usd = float(price.get("grandTotal", price.get("total", 0.0)))
        seats = offer.get("numberOfBookableSeats")

        results.append(
            FlightResult(
                flight_number=flight_number,
                airline=carrier,
                airline_name=get_airline_name(carrier),
                departure=departure_at,
                arrival=arrival_at,
                duration_minutes=duration_minutes,
                stops=stops,
                price_usd=price_usd,
                seats_available=seats,
                weather_at_dest=weather,
            )
        )
    return results


# Public tool: search flights
async def search_flights(
    origin: str,
    destination: str,
    date: str,
    passengers: int = 1,
    mock: bool = False,
) -> list[FlightResult]:
    cache_key = f"{origin}-{destination}-{date}-{passengers}"
    if cache_key in _flights_cache:
        logger.info("Cache hit for %s", cache_key)
        return _flights_cache[cache_key]

    logger.info("Cache miss for %s", cache_key)

    if mock:
        results = _mock_flights(origin, destination, date)
        results = [f for f in results if f.seats_available is None or f.seats_available >= passengers]
        _flights_cache[cache_key] = results
        return results

    token = await _get_amadeus_access_token()
    if token is None:
        logger.warning("No Amadeus token — falling back to mock for %s→%s", origin, destination)
        results = [f for f in _mock_flights(origin, destination, date) if f.seats_available is None or f.seats_available >= passengers]
        _flights_cache[cache_key] = results
        return results

    logger.info("Calling Amadeus flight-offers API for %s→%s", origin, destination)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(
                "https://test.api.amadeus.com/v2/shopping/flight-offers",
                headers=headers,
                params={
                    "originLocationCode": origin,
                    "destinationLocationCode": destination,
                    "departureDate": date,
                    "adults": passengers,
                    "max": 20,
                    "currencyCode": "USD",
                },
            )

        if response.status_code != 200:
            logger.warning(
                "Amadeus flight search returned %s — falling back to mock",
                response.status_code,
            )
            results = [f for f in _mock_flights(origin, destination, date) if f.seats_available is None or f.seats_available >= passengers]
            _flights_cache[cache_key] = results
            return results

        results = await _parse_amadeus_offers(response.json(), destination, date, mock=False)
        logger.info("Amadeus returned %s flights for %s→%s", len(results), origin, destination)
        _flights_cache[cache_key] = results
        return results

    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        logger.warning("Amadeus flight search failed (%s) — falling back to mock", exc)
        results = [f for f in _mock_flights(origin, destination, date) if f.seats_available is None or f.seats_available >= passengers]
        _flights_cache[cache_key] = results
        return results


# Public tool: weather
async def get_weather(city_code: str, date: str | None = None, mock: bool = False) -> str:
    cache_key = f"{city_code}-{date}"
    if cache_key in _weather_cache:
        logger.info("Weather cache hit for %s", cache_key)
        return _weather_cache[cache_key]

    if mock:
        result = _mock_weather(city_code)
        _weather_cache[cache_key] = result
        return result

    api_key = os.environ.get("OPENWEATHERMAP_API_KEY")
    if not api_key:
        logger.warning("OPENWEATHERMAP_API_KEY not set — using mock weather for %s", city_code)
        result = _mock_weather(city_code)
        _weather_cache[cache_key] = result
        return result

    city_name = IATA_CITY_NAMES.get(city_code, city_code)
    logger.info("Calling OpenWeatherMap forecast API for %s", city_name)
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(
                "https://api.openweathermap.org/data/2.5/forecast",
                params={
                    "q": city_name,
                    "appid": api_key,
                    "units": "metric",
                },
            )

        if response.status_code != 200:
            logger.warning(
                "OpenWeatherMap returned %s for %s — using mock", response.status_code, city_code
            )
            result = _mock_weather(city_code)
            _weather_cache[cache_key] = result
            return result

        result = _format_weather(response.json(), date)
        logger.info("Weather for %s: %s", city_code, result)
        _weather_cache[cache_key] = result
        return result

    except httpx.HTTPError as exc:
        logger.warning("Weather API failed for %s (%s) — using mock", city_code, exc)
        result = _mock_weather(city_code)
        _weather_cache[cache_key] = result
        return result


def _format_weather(payload: dict, date: str | None) -> str:
    entries = payload.get("list", [])
    if not entries:
        city = payload.get("city", {})
        return f"weather unavailable for {city.get('name', 'destination')}"

    if date:
        target_day = date[:10]  # YYYY-MM-DD
        matching = [e for e in entries if e.get("dt_txt", "").startswith(target_day)]
        chosen = matching[0] if matching else entries[0]

    chosen = entries[0]

    temp = chosen.get("main", {}).get("temp")
    description = chosen.get("weather", [{}])[0].get("description", "unknown")
    if temp is None:
        return "weather unavailable"
    return f"{round(temp)}°C, {description}"


# Mock data (deterministic — see phase-03-teaching.md section 5.1 point 4)
_MOCK_WEATHER: dict[str, str] = {
    "NRT": "15°C, partly cloudy",
    "SIN": "30°C, humid with chance of rain",
    "ICN": "8°C, clear",
    "BKK": "33°C, sunny",
    "DAD": "28°C, sunny",
    "SGN": "32°C, scattered showers",
    "HAN": "22°C, overcast",
    "HPH": "24°C, light rain",
    "HUI": "26°C, partly cloudy",
    "CXR": "29°C, sunny",
    "PQC": "30°C, clear",
    "VCA": "31°C, partly cloudy",
    "DLI": "20°C, cool and clear",
}


def _mock_weather(city_code: str) -> str:
    return _MOCK_WEATHER.get(city_code, "25°C, clear")


# Each mock flight: (airline, flight_no, dep_time, duration_min, stops, price, seats)
_MOCK_FLIGHTS: dict[str, list[tuple[str, str, str, int, int, float, int]]] = {
    "HAN-NRT": [
        ("VN", "VN310", "08:30", 330, 0, 310.00, 12),
        ("VJ", "VJ942", "09:15", 345, 1, 280.00, 5),
        ("NH", "NH898", "11:00", 315, 0, 450.00, 20),
        ("JL", "JL752", "13:45", 320, 0, 520.00, 8),
        ("VN", "VN312", "22:10", 360, 1, 380.00, 3),
    ],
    "SGN-NRT": [
        ("VN", "VN340", "07:50", 360, 0, 350.00, 14),
        ("VJ", "VJ870", "10:20", 390, 1, 320.00, 6),
        ("NH", "NH890", "23:40", 350, 0, 480.00, 10),
        ("JL", "JL770", "01:15", 370, 0, 460.00, 7),
    ],
    "SGN-SIN": [
        ("SQ", "SQ181", "06:30", 120, 0, 180.00, 22),
        ("VN", "VN601", "14:05", 135, 0, 120.00, 30),
        ("VJ", "VJ821", "19:50", 150, 1, 95.00, 4),
    ],
    "HAN-ICN": [
        ("VN", "VN416", "10:10", 240, 0, 350.00, 11),
        ("OZ", "OZ764", "13:30", 255, 0, 420.00, 9),
        ("KE", "KE484", "17:25", 270, 0, 380.00, 6),
    ],
    "SGN-BKK": [
        ("TG", "TG551", "08:00", 90, 0, 120.00, 18),
        ("VN", "VN603", "12:15", 105, 0, 95.00, 25),
        ("VJ", "VJ870", "16:40", 120, 1, 65.00, 2),
    ],
    "HAN-DAD": [
        ("VN", "VN171", "07:30", 75, 0, 70.00, 28),
        ("VJ", "VJ512", "18:55", 90, 0, 50.00, 16),
    ],
}


def find_mock_flight(flight_number: str) -> FlightResult | None:
    for route_key in _MOCK_FLIGHTS:
        origin, destination = route_key.split("-")
        for flights in _mock_flights(origin, destination):
            if flights.flight_number == flight_number:
                flights = _mock_flights(origin, destination)
                return next((f for f in flights if f.flight_number == flight_number), None)
    return None


def _mock_flights(origin: str, destination: str, date: str | None = None) -> list[FlightResult]:
    route_key = f"{origin}-{destination}"
    rows = _MOCK_FLIGHTS.get(route_key)
    if rows is None:
        raise ValueError(f"No mock data for {origin}→{destination}")

    weather = _mock_weather(destination)
    results: list[FlightResult] = []
    base_date = date or "2025-04-20"

    for airline, flight_number, dep_time, duration, stops, price, seats in rows:
        try:
            dep_dt = datetime.fromisoformat(f"{base_date}T{dep_time}:00")
        except ValueError:
            departure = dep_time
            arrival = dep_time
        else:
            arr_dt = dep_dt + timedelta(minutes=duration)
            departure = dep_dt.isoformat()
            arrival = arr_dt.isoformat()

        results.append(
            FlightResult(
                flight_number=flight_number,
                airline=airline,
                airline_name=get_airline_name(airline),
                departure=departure,
                arrival=arrival,
                duration_minutes=duration,
                stops=stops,
                price_usd=price,
                seats_available=seats,
                weather_at_dest=weather,
            )
        )
    return results
