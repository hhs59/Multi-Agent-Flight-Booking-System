import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent_system.llm import get_llm
from agent_system.models import TaskPlan
from agent_system.prompts import PLANNER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_BOOKING_REQUIRED_FIELDS = ("flight_number", "passenger_name", "passenger_email", "passport_number")


def _extract_json(text: str) -> str:
    text = text.strip()
    #Remove Markdown format
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _normalize_plan_dict(data: dict) -> dict:
    bd = data.get("booking_details")
    if isinstance(bd, dict) and any(bd.get(f) is None for f in _BOOKING_REQUIRED_FIELDS):
        data["booking_details"] = None
        data["needs_clarification"] = True
    return data


async def planner_node(state: dict) -> dict:
    query = state["query"]
    history = state.get("conversation_history", [])

    llm = get_llm(temperature=0.0)
    messages = [SystemMessage(content=PLANNER_SYSTEM_PROMPT)]
    for msg in history[-10:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    messages.append(HumanMessage(content=query))

    try:
        response = await llm.ainvoke(messages)
        raw = _extract_json(response.content)
        data = _normalize_plan_dict(json.loads(raw))
        plan = TaskPlan.model_validate(data)
    except Exception as exc:
        logger.warning("Planner parse failed: %s", exc)
        plan = TaskPlan(
            intent="unclear",
            reasoning="Failed to parse LLM output.",
            needs_clarification=True,
            clarification_question="Could you clarify what you'd like to do? I can search flights, advise on prices, or book a flight.",
            language="en",
        )
        return {
            "plan": plan,
            "final_response": plan.clarification_question,
            "errors": [f"planner_parse_error: {exc}"],
        }

    logger.info(
        "Planner: intent=%s language=%s origin=%s destination=%s",
        plan.intent,
        plan.language,
        plan.flight_query.origin if plan.flight_query else None,
        plan.flight_query.destination if plan.flight_query else None,
    )

    if plan.intent == "unclear":
        if not plan.clarification_question:
            plan = plan.model_copy(update={
                "clarification_question": "Could you clarify what you'd like to do?" if plan.language == "en" else "Bạn có thể nói rõ hơn bạn muốn làm gì không?",
            })
        return {"plan": plan, "final_response": plan.clarification_question}

    return {"plan": plan}
