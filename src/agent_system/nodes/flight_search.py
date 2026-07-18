import logging

from agent_system.models import FlightSearchOutput
from agent_system.tools.flight_tools import search_flights

logger = logging.getLogger(__name__)

_PRIORITY_SORTS = {
    "price": lambda f: f.price_usd,
    "speed": lambda f: f.duration_minutes,
    "comfort": lambda f: (f.stops, f.price_usd),
    "balanced": lambda f: f.price_usd + f.stops * 50 + f.duration_minutes * 0.1,
}


async def flight_search_node(state: dict) -> dict:
    plan = state.get("plan")
    if not plan or not plan.flight_query:
        return {
            "flight_results": FlightSearchOutput(results=[], total_found=0, search_params={}),
            "errors": ["flight_search: no flight_query in plan"],
        }

    query_flight = plan.flight_query
    mock = state.get("mock_mode", True)

    try:
        flights = await search_flights(
            origin=query_flight.origin,
            destination=query_flight.destination,
            date=query_flight.departure_date.isoformat(),
            passengers=query_flight.passengers,
            mock=mock,
        )
    except ValueError as exc:
        logger.warning("Unknown route %s->%s: %s", query_flight.origin, query_flight.destination, exc)
        return {
            "flight_results": FlightSearchOutput(results=[], total_found=0, search_params={}),
            "errors": [f"flight_search: unknown route {query_flight.origin}->{query_flight.destination}"],
        }
    except Exception as exc:
        logger.warning("Flight search failed: %s", exc)
        return {
            "flight_results": FlightSearchOutput(results=[], total_found=0, search_params={}),
            "errors": [f"flight_search: {exc}"],
        }

    sort_key = _PRIORITY_SORTS.get(query_flight.priority, _PRIORITY_SORTS["balanced"])
    flights = sorted(flights, key=sort_key)

    logger.info(
        "Found %d flights %s->%s, sorted by %s",
        len(flights),
        query_flight.origin,
        query_flight.destination,
        query_flight.priority,
    )

    return {
        "flight_results": FlightSearchOutput(
            results=flights,
            total_found=len(flights),
            search_params={
                "origin": query_flight.origin,
                "destination": query_flight.destination,
                "date": query_flight.departure_date.isoformat(),
                "passengers": query_flight.passengers,
                "priority": query_flight.priority,
            },
        )
    }
