import logging

from agent_system.models import FlightPriceAnalysis, PriceIntelligenceOutput
from agent_system.tools.price_tools import analyze_price_trend, get_price_history, store_prices_bulk

logger = logging.getLogger(__name__)


async def price_intel_node(state: dict) -> dict:
    flight_results = state.get("flight_results")
    if not flight_results or not flight_results.results:
        return {
            "price_intelligence": PriceIntelligenceOutput(
                flights=[], best_deal=None, summary="No flights to analyze."
            )
        }

    mock = state.get("mock_mode", True)
    origin = flight_results.search_params["origin"]
    destination = flight_results.search_params["destination"]

    try:
        history = get_price_history(origin=origin, destination=destination, mock=mock)
    except Exception as exc:
        logger.warning("Price history fetch failed: %s", exc)
        return {
            "price_intelligence": PriceIntelligenceOutput(
                flights=[], best_deal=None, summary="Price history unavailable."
            ),
            "errors": [f"price_intel: {exc}"],
        }

    if not mock:
        await store_prices_bulk([(
            origin, destination,
            flight_results.search_params["date"],
            min(f.price_usd for f in flight_results.results),
            "amadeus",
        )])

    analyses: list[FlightPriceAnalysis] = []
    errors: list[str] = []

    for flight in flight_results.results:
        try:
            analysis = analyze_price_trend(history, current_price=flight.price_usd)
            analyses.append(FlightPriceAnalysis(flight=flight, price_analysis=analysis))
        except Exception as exc:
            logger.warning("Price analysis failed for %s: %s", flight.flight_number, exc)
            errors.append(f"price_intel: {flight.flight_number}: {exc}")

    if not analyses:
        return {
            "price_intelligence": PriceIntelligenceOutput(
                flights=[], best_deal=None, summary="Price analysis failed for all flights."
            ),
            "errors": errors,
        }

    buy_now = [a for a in analyses if a.price_analysis.prediction == "buy_now"]
    if buy_now:
        best = min(buy_now, key=lambda a: a.flight.price_usd).flight
    else:
        best = min(analyses, key=lambda a: a.flight.price_usd).flight

    summary = (
        f"Analyzed {len(analyses)} flights. "
        f"{len(buy_now)} recommended to buy now. "
        f"Best deal: {best.flight_number} at ${best.price_usd:.0f}."
    )

    logger.info("Price intel: %d flights, best=%s", len(analyses), best.flight_number)

    result = {
        "price_intelligence": PriceIntelligenceOutput(
            flights=analyses, best_deal=best, summary=summary
        )
    }
    if errors:
        result["errors"] = errors
    return result
