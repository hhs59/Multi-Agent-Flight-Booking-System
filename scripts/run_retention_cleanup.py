"""Run account and search retention once as a separately scheduled worker."""

from __future__ import annotations

import json
import logging
import os

from agent_system.auth.bootstrap import auth_runtime_from_environment
from agent_system.services.account_lifecycle import AccountLifecycleService
from agent_system.services.retention import SearchRetentionService

logger = logging.getLogger("flight.retention_cleanup")


def run_once(*, batch_size: int | None = None) -> dict[str, int]:
    """Purge due accounts and expired search projections in one DB transaction."""

    effective_batch_size = batch_size
    if effective_batch_size is None:
        effective_batch_size = int(os.getenv("SEARCH_RETENTION_BATCH_SIZE", "500"))

    auth = auth_runtime_from_environment()
    with auth.session_factory() as session, session.begin():
        accounts_deleted = AccountLifecycleService(session, auth.encryptor).purge_due_accounts()
        searches = SearchRetentionService(session).purge_expired(batch_size=effective_batch_size)

    return {
        "accounts_deleted": int(accounts_deleted or 0),
        "offers_deleted": searches.offers_deleted,
        "attempts_deleted": searches.attempts_deleted,
        "searches_deleted": searches.searches_deleted,
    }


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    result = run_once()
    logger.info("retention cleanup completed: %s", result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
