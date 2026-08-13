from __future__ import annotations

from collections import deque
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from agent_system.auth.router import AuthRuntime
from agent_system.auth.sessions import (
    SessionAuthenticationError,
    SessionService,
)
from agent_system.repositories.sessions import SessionRepository
from agent_system.security.sanitization import sanitize_payload


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    version: str = "0.1.0"


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    database: str
    dependencies: dict[str, str]


class ProviderCapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    environment: str
    capabilities: dict[str, bool]
    available: bool


class TraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    snapshot: dict


class MetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_count: int
    error_count: int
    average_latency_ms: float
    trace_count: int


_trace_store: deque = deque(maxlen=100)
_trace_lock = Lock()
_request_count = 0
_error_count = 0
_latency_total_ms = 0.0


def store_trace(trace_id: str, snapshot: dict) -> None:
    with _trace_lock:
        _trace_store.append({"trace_id": trace_id, "snapshot": sanitize_payload(snapshot)})


def get_trace(trace_id: str) -> dict | None:
    with _trace_lock:
        for entry in _trace_store:
            if entry["trace_id"] == trace_id:
                return entry["snapshot"]
    return None


def record_request(latency_ms: float, error: bool = False) -> None:
    global _request_count, _error_count, _latency_total_ms
    with _trace_lock:
        _request_count += 1
        if error:
            _error_count += 1
        _latency_total_ms += latency_ms


def create_operations_router(
    runtime: AuthRuntime | None = None,
    provider_registry=None,
) -> APIRouter:
    router = APIRouter(tags=["operations"])

    @router.get("/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            status="ok",
            version="0.1.0",
        )

    @router.get("/ready", response_model=ReadinessResponse)
    async def readiness():
        db_status = "ok"
        db_details = "connected"
        if runtime is not None:
            try:
                session = runtime.session_factory()
                session.execute(__import__("sqlalchemy").text("SELECT 1"))
                session.close()
            except Exception as exc:
                db_status = "error"
                db_details = str(exc)

        deps: dict[str, str] = {"cache": "ok"}
        if provider_registry is not None:
            flight_name = getattr(provider_registry.flight, "name", "unknown")
            deps["flight_provider"] = (
                f"{flight_name} ({getattr(provider_registry.flight, 'environment', 'unknown').value})"
            )

        overall = "ok" if db_status == "ok" else "degraded"
        return ReadinessResponse(
            status=overall,
            database=db_status,
            dependencies=dict(deps) | {"database": db_details},
        )

    @router.get("/providers", response_model=list[ProviderCapabilityResponse])
    async def provider_capabilities():
        """List available providers and their capabilities - safe for users."""
        providers: list[ProviderCapabilityResponse] = []
        if provider_registry is not None:
            flight = provider_registry.flight
            providers.append(
                ProviderCapabilityResponse(
                    name=flight.name,
                    environment=flight.environment.value,
                    capabilities={
                        "can_search": True,
                        "can_reprice": getattr(flight, "capabilities", None) is not None,
                        "can_book": False,
                        "can_hold": False,
                        "can_cancel": False,
                        "can_refund": False,
                    },
                    available=flight.available if hasattr(flight, "available") else True,
                )
            )
        if not providers:
            providers.append(
                ProviderCapabilityResponse(
                    name="mock",
                    environment="mock",
                    capabilities={
                        "can_search": True,
                        "can_reprice": True,
                        "can_book": True,
                        "can_hold": True,
                        "can_cancel": True,
                        "can_refund": True,
                    },
                    available=True,
                )
            )
        return providers

    @router.get("/traces/{trace_id}", response_model=TraceResponse)
    async def get_trace_endpoint(trace_id: str, request: Request):
        """Traces available only to authorized operators."""
        if runtime is not None:
            try:
                session = runtime.session_factory()
                with session.begin():
                    auth = SessionService(
                        SessionRepository(session),
                        runtime.token_hasher,
                        runtime.session_settings,
                    )
                    session_token = request.cookies.get(runtime.session_settings.cookie_name)
                    auth.authenticate(session_token)
                session.close()
            except SessionAuthenticationError as exc:
                raise HTTPException(
                    status_code=403, detail="traces require operator authorization"
                ) from exc
            except Exception:
                pass

        snapshot = get_trace(trace_id)
        if snapshot is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"Trace not found: {trace_id}"},
            )
        return TraceResponse(trace_id=trace_id, snapshot=snapshot)

    @router.get("/metrics", response_model=MetricsResponse)
    async def metrics_endpoint():
        with _trace_lock:
            avg = _latency_total_ms / _request_count if _request_count else 0.0
            return MetricsResponse(
                request_count=_request_count,
                error_count=_error_count,
                average_latency_ms=round(avg, 1),
                trace_count=len(_trace_store),
            )

    return router
