import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from agent_system.llm import extract_json, get_llm
from agent_system.models import TripAdvice
from agent_system.prompts import ADVISOR_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _build_context(state: dict) -> str:
    sections: list[str] = []
    errors = state.get("errors", [])

    flight_results = state.get("flight_results")
    if flight_results and flight_results.results:
        lines = [f"- {f.flight_number} ({f.airline_name}): ${f.price_usd:.0f}, "
                 f"{f.duration_minutes}min, {f.stops} stop(s), dep {f.departure}"
                 for f in flight_results.results]
        sections.append("## Flights\n" + "\n".join(lines))
        sections.append(f"Weather at destination: {flight_results.results[0].weather_at_dest}")
    else:
        sections.append("## Flights\nNo flight results available.")

    price_intel = state.get("price_intelligence")
    if price_intel and price_intel.flights:
        lines = [
            f"- {a.flight.flight_number}: ${a.flight.price_usd:.0f}, "
            f"{a.price_analysis.prediction.replace('_', ' ')} "
            f"(confidence {a.price_analysis.confidence:.0%}, "
            f"percentile {a.price_analysis.price_percentile}, "
            f"trend {a.price_analysis.trend})"
            for a in price_intel.flights
        ]
        sections.append("## Price Intelligence\n" + "\n".join(lines))
        if price_intel.best_deal:
            sections.append(f"Best deal: {price_intel.best_deal.flight_number} at ${price_intel.best_deal.price_usd:.0f}")
    else:
        sections.append("## Price Intelligence\nNo price analysis available.")

    review_analysis = state.get("review_analysis")
    if review_analysis and review_analysis.reviews:
        lines = [
            f"- {code}: {r.overall_rating}/5, {r.sentiment_summary}"
            for code, r in review_analysis.reviews.items()
        ]
        sections.append("## Airline Reviews\n" + "\n".join(lines))
        sections.append(f"Comparison: {review_analysis.comparison}")
        sections.append(f"Recommendation: {review_analysis.recommendation}")
    else:
        sections.append("## Airline Reviews\nNo review data available.")

    plan = state.get("plan")
    if plan:
        sections.append(f"## User\nLanguage: {plan.language}, Intent: {plan.intent}")
        if plan.flight_query:
            q = plan.flight_query
            sections.append(
                f"Route: {q.origin}->{q.destination}, date {q.departure_date}, "
                f"{q.passengers} pax, priority {q.priority}"
            )

    if errors:
        sections.append("## Limitations\nSome data may be incomplete: " + "; ".join(errors))

    return "\n\n".join(sections)


def _try_parse_trip_advice(text: str) -> TripAdvice | None:
    try:
        raw = extract_json(text)
        data = json.loads(raw)
        return TripAdvice.model_validate(data)
    except Exception:
        return None


async def advisor_node(state: dict) -> dict:
    plan = state.get("plan")
    language = plan.language if plan else "en"

    context = _build_context(state)

    llm = get_llm(temperature=0.7)
    messages = [
        SystemMessage(content=ADVISOR_SYSTEM_PROMPT),
        HumanMessage(content=f"User language: {language}\n\n{context}"),
    ]

    try:
        response = await llm.ainvoke(messages)
        text = response.content.strip()
    except Exception as exc:
        logger.error("Advisor LLM call failed: %s", exc)
        fallback = (
            "I'm unable to generate advice right now. Please try again later."
            if language == "en"
            else "Tôi không thể tạo khuyến nghị lúc này. Vui lòng thử lại sau."
        )
        return {
            "final_response": fallback,
            "trip_advice": None,
            "errors": [f"advisor: {exc}"],
        }

    if not text:
        text = (
            "No advice could be generated from the available data."
            if language == "en"
            else "Không thể tạo khuyến nghị từ dữ liệu hiện có."
        )

    trip_advice = _try_parse_trip_advice(text)

    return {
        "final_response": text,
        "trip_advice": trip_advice,
    }
