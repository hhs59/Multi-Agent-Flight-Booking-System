import logging

from agent_system.models import ReviewAnalysisOutput
from agent_system.tools.review_tools import analyze_reviews, compare_airlines, get_airline_reviews

logger = logging.getLogger(__name__)


async def review_analyzer_node(state: dict) -> dict:
    flight_results = state.get("flight_results")
    if not flight_results or not flight_results.results:
        return {
            "review_analysis": ReviewAnalysisOutput(
                reviews={},
                comparison="No flights to analyze — no airline reviews available.",
                recommendation="Search for flights first to see airline reviews.",
            )
        }

    mock = state.get("mock_mode", True)
    airlines = sorted({f.airline for f in flight_results.results if f.airline})

    if not airlines:
        return {
            "review_analysis": ReviewAnalysisOutput(
                reviews={},
                comparison="No airlines identified in flight results.",
                recommendation="Unable to analyze reviews without airline information.",
            )
        }

    plan = state.get("plan")
    priority = "balanced"
    if plan and plan.flight_query:
        priority = plan.flight_query.priority

    reviews: dict[str, any] = {}
    errors: list[str] = []

    for code in airlines:
        try:
            raw = await get_airline_reviews(code, mock=mock)
            reviews[code] = analyze_reviews(raw, airline_code=code)
        except Exception as exc:
            logger.warning("Review analysis failed for %s: %s", code, exc)
            errors.append(f"review_analyzer: {code}: {exc}")

    if not reviews:
        return {
            "review_analysis": ReviewAnalysisOutput(
                reviews={},
                comparison="All airline review analyses failed.",
                recommendation="Unable to provide airline recommendations at this time.",
            ),
            "errors": errors,
        }

    comparison, recommendation = compare_airlines(reviews, priority=priority)

    logger.info("Review analysis: %d/%d airlines analyzed", len(reviews), len(airlines))

    result = {
        "review_analysis": ReviewAnalysisOutput(
            reviews=reviews,
            comparison=comparison,
            recommendation=recommendation,
        )
    }
    if errors:
        result["errors"] = errors
    return result
