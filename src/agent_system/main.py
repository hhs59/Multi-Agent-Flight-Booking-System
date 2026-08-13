import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agent_system.api import create_product_router
from agent_system.api.operations import create_operations_router, record_request
from agent_system.auth.bootstrap import auth_runtime_from_environment
from agent_system.auth.router import create_auth_router
from agent_system.logging_config import setup_logging
from agent_system.providers.errors import (
    CapabilityUnavailable,
    OfferExpiredError,
    OfferUnavailableError,
    PriceDiscrepancyError,
    ProviderError,
    ProviderTimeoutError,
    ProviderValidationError,
)
from agent_system.services.booking_workflow import BookingGateSettings, BookingWorkflowService
from agent_system.services.feature_settings import FeatureSettings
from agent_system.services.orchestration import OrchestrationService
from agent_system.services.watch_worker import WatchWorker
from agent_system.services.watches import WatchGateSettings, WatchService

logger = logging.getLogger(__name__)

setup_logging()

feature_settings = FeatureSettings.from_environment()

orchestration_service: OrchestrationService | None = None
booking_workflow_service: BookingWorkflowService | None = None
watch_service: WatchService | None = None
watch_worker_service: WatchWorker | None = None


@asynccontextmanager
async def application_lifespan(_: FastAPI):
    try:
        yield
    finally:
        if orchestration_service is not None:
            await orchestration_service.aclose()


app = FastAPI(
    title="AI Flight Advisor",
    version="0.1.0",
    lifespan=application_lifespan,
)

auth_runtime = None
provider_registry = None
if os.environ.get("IDENTITY_ENABLED", "false").lower() == "true":
    auth_runtime = auth_runtime_from_environment()
    orchestration_service = OrchestrationService.from_environment(
        auth_runtime.session_factory,
        feature_settings=feature_settings,
    )
    if orchestration_service.provider_registry is None:
        raise RuntimeError("provider registry is required")
    provider_registry = orchestration_service.provider_registry
    booking_workflow_service = BookingWorkflowService(
        auth_runtime.session_factory,
        flight_provider=provider_registry.flight,
        payment_provider=provider_registry.payment,
        flight_search=orchestration_service.flight_search,
        encryptor=auth_runtime.encryptor,
        gate_settings=BookingGateSettings.from_environment(),
    )
    watch_service = WatchService(
        auth_runtime.session_factory,
        auth_runtime.encryptor,
        execution_mode=provider_registry.flight.environment,
        gates=WatchGateSettings.from_environment(),
    )
    watch_worker_service = WatchWorker(
        auth_runtime.session_factory,
        flight_provider=provider_registry.flight,
        flight_search=orchestration_service.flight_search,
        notification_provider=provider_registry.notifications,
        encryptor=auth_runtime.encryptor,
        booking_workflow=booking_workflow_service,
        gates=WatchGateSettings.from_environment(),
    )
    app.include_router(create_auth_router(auth_runtime))
    app.include_router(
        create_product_router(
            auth_runtime,
            orchestration_service,
            booking_workflow_service,
            watch_service,
            watch_worker_service,
        )
    )

# Operations endpoints are available in every mode and registered exactly once.
app.state.feature_settings = feature_settings
app.include_router(create_operations_router(auth_runtime, provider_registry))

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    ).split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    started_at = time.time()

    response = await call_next(request)
    latency_ms = (time.time() - started_at) * 1000
    record_request(latency_ms, error=response.status_code >= 500)

    response.headers["X-Trace-Id"] = trace_id
    logger.info(
        "%s %s %d %.0fms",
        request.method,
        request.url.path,
        response.status_code,
        latency_ms,
    )
    return response


@app.exception_handler(ProviderError)
async def provider_exception_handler(request: Request, exc: ProviderError):
    trace_id = getattr(request.state, "trace_id", "unknown")
    if isinstance(exc, CapabilityUnavailable):
        status_code = 501
    elif isinstance(exc, ProviderValidationError):
        status_code = 400
    elif isinstance(
        exc,
        (OfferExpiredError, OfferUnavailableError, PriceDiscrepancyError),
    ):
        status_code = 409
    elif isinstance(exc, ProviderTimeoutError):
        status_code = 504
    else:
        status_code = 503
    logger.warning(
        "Provider operation failed (trace_id=%s provider=%s operation=%s error=%s)",
        trace_id,
        exc.provider,
        exc.operation,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=status_code,
        content={
            "error": exc.safe_message,
            "error_type": type(exc).__name__,
            "trace_id": trace_id,
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "unknown")
    logger.exception("Unhandled exception (trace_id=%s): %s", trace_id, exc)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "trace_id": trace_id},
    )
