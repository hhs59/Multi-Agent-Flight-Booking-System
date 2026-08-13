"""Run the Phase 7 watch worker as a separate process.

This process owns scheduling/leases for watches; FastAPI intentionally does not import or start it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlsplit

from agent_system.auth.bootstrap import auth_runtime_from_environment
from agent_system.providers.cache import SearchCache
from agent_system.providers.clock import SystemClock
from agent_system.providers.registry import build_provider_registry
from agent_system.providers.resilience import ProviderExecutor
from agent_system.providers.settings import ProviderSettings
from agent_system.services.booking_workflow import BookingGateSettings, BookingWorkflowService
from agent_system.services.flight_search import FlightSearchService
from agent_system.services.watch_worker import WatchWorker
from agent_system.services.watches import WatchGateSettings

logger = logging.getLogger("flight.watch_worker")


def _safe_database_target(value: str) -> str:
    """Return a non-secret database target for startup diagnostics."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return "configured database"
    if not parsed.hostname:
        return "configured database"
    host = parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    database = parsed.path.lstrip("/") or "(default)"
    return f"{host}{port}/{database}"


async def run() -> None:
    auth = auth_runtime_from_environment()
    settings = ProviderSettings.from_environment()
    clock = SystemClock()
    registry = build_provider_registry(settings, clock=clock)
    search = FlightSearchService(
        registry.flight,
        SearchCache(max_entries=settings.search_cache_max_entries),
        ProviderExecutor.from_settings(settings, clock=clock),
        clock,
    )
    booking = BookingWorkflowService(
        auth.session_factory,
        flight_provider=registry.flight,
        payment_provider=registry.payment,
        flight_search=search,
        encryptor=auth.encryptor,
        gate_settings=BookingGateSettings.from_environment(),
        clock=clock,
    )
    worker = WatchWorker(
        auth.session_factory,
        flight_provider=registry.flight,
        flight_search=search,
        notification_provider=registry.notifications,
        encryptor=auth.encryptor,
        booking_workflow=booking,
        gates=WatchGateSettings.from_environment(),
        clock=clock,
        lease_seconds=int(os.getenv("WATCH_WORKER_LEASE_SECONDS", "120")),
        interval_seconds=int(os.getenv("WATCH_WORKER_INTERVAL_SECONDS", "900")),
    )
    interval = max(1, int(os.getenv("WATCH_WORKER_POLL_SECONDS", "5")))
    logger.info(
        "watch worker starting mode=%s flight_provider=%s notification_provider=%s "
        "poll_seconds=%s watch_interval_seconds=%s database=%s",
        settings.execution_mode.value,
        settings.flight_provider,
        settings.notification_provider,
        interval,
        worker.interval_seconds,
        _safe_database_target(os.getenv("DATABASE_URL", "configured database")),
    )
    try:
        while True:
            result = await worker.run_once()
            await worker.expire_holds()
            if result is not None:
                logger.info("watch worker run completed: %s", result)
            await asyncio.sleep(interval)
    finally:
        await registry.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(run())
