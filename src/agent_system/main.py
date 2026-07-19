import logging
import os
import time
import uuid
from collections import deque
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from agent_system.graph import invoke_agent
from agent_system.logging_config import setup_logging
from agent_system.models import (
    AgentResponse,
    BookingConfirmation,
    BookingDetails,
    FlightResult,
)
from agent_system.tools.booking_tools import create_booking
from agent_system.tools.flight_tools import find_mock_flight

logger = logging.getLogger(__name__)

setup_logging()

app = FastAPI(title="AI Flight Advisor", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

_trace_store: deque = deque(maxlen=100)
_trace_lock = Lock()
_request_count = 0
_error_count = 0
_latency_total_ms = 0.0


def _store_trace(trace_id: str, snapshot: dict) -> None:
    with _trace_lock:
        _trace_store.append({"trace_id": trace_id, "snapshot": snapshot})


def _get_trace(trace_id: str) -> dict | None:
    with _trace_lock:
        for entry in _trace_store:
            if entry["trace_id"] == trace_id:
                return entry["snapshot"]
    return None


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    global _request_count, _error_count, _latency_total_ms

    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    request.state.start_time = time.time()

    response = await call_next(request)
    latency_ms = (time.time() - request.state.start_time) * 1000

    with _trace_lock:
        _request_count += 1
        _latency_total_ms += latency_ms
        if response.status_code >= 500:
            _error_count += 1

    response.headers["X-Trace-Id"] = trace_id
    logger.info(
        "%s %s %d %.0fms",
        request.method,
        request.url.path,
        response.status_code,
        latency_ms,
    )
    return response


class SearchRequest(BaseModel):
    query: str
    mock: bool = True


class ChatRequest(BaseModel):
    message: str
    conversation_history: list[dict[str, str]] = []
    mock: bool = True
    selected_flight: FlightResult | None = None
    pending_booking: dict | None = None


class BookRequest(BaseModel):
    flight_number: str
    passenger_name: str
    passenger_email: EmailStr
    passport_number: str
    phone: str | None = None
    selected_flight: FlightResult | None = None
    mock: bool = True


class AgentAPIResponse(BaseModel):
    agent: AgentResponse
    conversation_history: list[dict[str, str]]
    trace_id: str
    latency_ms: float


class BookingAPIResponse(BaseModel):
    booking_confirmation: BookingConfirmation | None
    response: str
    trace_id: str
    latency_ms: float


class HealthResponse(BaseModel):
    status: str
    agents: list[str]
    mock_mode_supported: bool
    version: str


class MetricsResponse(BaseModel):
    request_count: int
    error_count: int
    average_latency_ms: float
    trace_count: int
    generation_metrics: dict | None = None


class TraceResponse(BaseModel):
    trace_id: str
    snapshot: dict


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.exception("Unhandled exception (trace_id=%s): %s", trace_id, exc)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "trace_id": trace_id},
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        agents=["planner", "flight_search", "price_intelligence", "review_analyzer", "booking", "advisor"],
        mock_mode_supported=True,
        version="0.1.0",
    )


@app.post("/search", response_model=AgentAPIResponse)
async def search(req: SearchRequest, request: Request):
    trace_id = request.state.trace_id
    start = time.time()

    agent_response, history = await invoke_agent(
        query=req.query,
        history=[],
        mock_mode=req.mock,
    )

    latency_ms = (time.time() - start) * 1000

    _store_trace(trace_id, {"agent": agent_response.model_dump(mode="json"), "history": history})

    return AgentAPIResponse(
        agent=agent_response,
        conversation_history=history,
        trace_id=trace_id,
        latency_ms=latency_ms,
    )


@app.post("/chat", response_model=AgentAPIResponse)
async def chat(req: ChatRequest, request: Request):
    trace_id = request.state.trace_id
    start = time.time()

    agent_response, history = await invoke_agent(
        query=req.message,
        history=req.conversation_history,
        mock_mode=req.mock,
        selected_flight=req.selected_flight,
        pending_booking=req.pending_booking,
    )

    latency_ms = (time.time() - start) * 1000

    _store_trace(trace_id, {"agent": agent_response.model_dump(mode="json"), "history": history})

    return AgentAPIResponse(
        agent=agent_response,
        conversation_history=history,
        trace_id=trace_id,
        latency_ms=latency_ms,
    )


@app.post("/book", response_model=BookingAPIResponse)
async def book(req: BookRequest, request: Request):
    trace_id = request.state.trace_id
    start = time.time()

    flight = req.selected_flight
    if flight is None:
        flight = find_mock_flight(req.flight_number)
    if flight is None or flight.flight_number != req.flight_number:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown flight number: {req.flight_number}", "trace_id": trace_id},
        )

    details = BookingDetails(
        flight_number=req.flight_number,
        passenger_name=req.passenger_name,
        passenger_email=req.passenger_email,
        passport_number=req.passport_number,
        phone=req.phone,
    )

    confirmation = await create_booking(
        flight_number=req.flight_number,
        passenger_details=details,
        mock=req.mock,
    )

    latency_ms = (time.time() - start) * 1000

    response_text = (
        f"Booking confirmed! Code: {confirmation.confirmation_code}"
        if confirmation.status == "confirmed"
        else f"Booking failed (status: {confirmation.status})."
    )

    _store_trace(trace_id, {"booking_confirmation": confirmation.model_dump(mode="json")})

    return BookingAPIResponse(
        booking_confirmation=confirmation,
        response=response_text,
        trace_id=trace_id,
        latency_ms=latency_ms,
    )


@app.get("/trace/{trace_id}", response_model=TraceResponse)
async def get_trace(trace_id: str):
    snapshot = _get_trace(trace_id)
    if snapshot is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Trace not found: {trace_id}"},
        )
    return TraceResponse(trace_id=trace_id, snapshot=snapshot)


@app.get("/metrics", response_model=MetricsResponse)
async def metrics():
    with _trace_lock:
        avg = _latency_total_ms / _request_count if _request_count else 0.0
        return MetricsResponse(
            request_count=_request_count,
            error_count=_error_count,
            average_latency_ms=round(avg, 1),
            trace_count=len(_trace_store),
        )
