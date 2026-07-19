import asyncio
import logging
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph

from agent_system.models import (
    AgentResponse,
    BookingConfirmation,
    Citation,
    FlightResult,
    FlightSearchOutput,
    PriceIntelligenceOutput,
    ReviewAnalysisOutput,
    TaskPlan,
    TripAdvice,
)
from agent_system.nodes.advisor import advisor_node
from agent_system.nodes.booking import booking_node
from agent_system.nodes.flight_search import flight_search_node
from agent_system.nodes.planner import planner_node
from agent_system.nodes.price_intel import price_intel_node
from agent_system.nodes.review_analyzer import review_analyzer_node

logger = logging.getLogger(__name__)


def _append_errors(left: list[str], right: list[str]) -> list[str]:
    return (left or []) + (right or [])


class AgentState(TypedDict, total=False):
    query: str
    conversation_history: list[dict[str, str]]
    plan: TaskPlan | None
    flight_results: FlightSearchOutput | None
    price_intelligence: PriceIntelligenceOutput | None
    review_analysis: ReviewAnalysisOutput | None
    booking_confirmation: BookingConfirmation | None
    trip_advice: TripAdvice | None
    retrieval_context: list[Citation] | None
    selected_flight: FlightResult | None
    pending_booking: dict | None
    final_response: str | None
    errors: Annotated[list[str], _append_errors]
    tokens_used: int
    mock_mode: bool


def route_after_planner(state: dict) -> str:
    plan = state.get("plan")
    if plan is None:
        return END
    if plan.intent in ("search", "advise"):
        return "flight_search"
    if plan.intent == "book":
        return "booking"
    return END


def build_agent_graph():
    graph = StateGraph(AgentState)
    graph.add_node("planner", planner_node)
    graph.add_node("flight_search", flight_search_node)
    graph.add_node("price_intelligence", price_intel_node)
    graph.add_node("review_analyzer", review_analyzer_node)
    graph.add_node("booking", booking_node)
    graph.add_node("advisor", advisor_node)

    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", route_after_planner)
    graph.add_edge("flight_search", "price_intelligence")
    graph.add_edge("price_intelligence", "review_analyzer")
    graph.add_edge("review_analyzer", "advisor")
    graph.add_edge("advisor", END)
    graph.add_edge("booking", END)

    return graph.compile()


_compiled = None


def _get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_agent_graph()
    return _compiled


def _build_agent_response(state: dict) -> AgentResponse:
    plan = state.get("plan")
    if plan is None:
        plan = TaskPlan(
            intent="unclear",
            reasoning="No plan produced.",
            needs_clarification=True,
            clarification_question="Could you clarify what you'd like to do?",
            language="en",
        )

    response = state.get("final_response")
    if not response and plan.intent == "unclear":
        response = plan.clarification_question or "Could you clarify what you'd like to do?"
    if not response:
        response = "I couldn't process your request."

    return AgentResponse(
        response=response,
        plan=plan,
        trip_advice=state.get("trip_advice"),
        flight_results=state.get("flight_results"),
        price_intelligence=state.get("price_intelligence"),
        review_analysis=state.get("review_analysis"),
        booking_confirmation=state.get("booking_confirmation"),
        retrieval_context=state.get("retrieval_context"),
        tokens_used=state.get("tokens_used", 0),
        errors=state.get("errors", []),
        language=plan.language,
    )


async def invoke_agent(
    query: str,
    history: list[dict[str, str]] | None = None,
    mock_mode: bool = True,
    selected_flight: FlightResult | None = None,
    pending_booking: dict | None = None,
) -> tuple[AgentResponse, list[dict[str, str]]]:
    
    history = list(history or [])
    history.append({"role": "user", "content": query})

    initial_state: AgentState = {
        "query": query,
        "conversation_history": history,
        "plan": None,
        "flight_results": None,
        "price_intelligence": None,
        "review_analysis": None,
        "booking_confirmation": None,
        "trip_advice": None,
        # "retrieval_context": None,
        "selected_flight": selected_flight,
        "pending_booking": pending_booking,
        "final_response": None,
        "errors": [],
        # "tokens_used": 0,
        "mock_mode": mock_mode,
    }

    graph = _get_graph()
    try:
        final_state = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.error("Graph invocation failed: %s", exc)
        final_state = {
            **initial_state,
            "errors": [f"graph_error: {exc}"],
            "final_response": "An error occurred while processing your request.",
        }

    agent_response = _build_agent_response(final_state)

    history.append({"role": "assistant", "content": agent_response.response})
    history = history[-10:]

    return agent_response, history


def invoke_agent_sync(
    query: str,
    history: list[dict[str, str]] | None = None,
    mock_mode: bool = True,
    selected_flight: FlightResult | None = None,
    pending_booking: dict | None = None,
) -> tuple[AgentResponse, list[dict[str, str]]]:
    return asyncio.run(
        invoke_agent(query, history, mock_mode, selected_flight, pending_booking)
    )
