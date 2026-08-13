from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def normalize_database_url(database_url: str) -> str:
    """Use the psycopg v3 SQLAlchemy dialect for generic PostgreSQL URLs."""
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg2://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


def create_database_engine(
    database_url: str,
    *,
    allow_sqlite_for_tests: bool = False,
    echo: bool = False,
) -> Engine:
    database_url = normalize_database_url(database_url)
    if database_url.startswith("sqlite") and not allow_sqlite_for_tests:
        raise ValueError("SQLite is permitted only for isolated unit tests")
    if not database_url.startswith(("postgresql", "sqlite")):
        raise ValueError("DATABASE_URL must use PostgreSQL")
    engine_options: dict = {"echo": echo, "pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        engine_options["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            engine_options["poolclass"] = StaticPool
    engine = create_engine(database_url, **engine_options)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
            del connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def engine_from_environment(*, allow_sqlite_for_tests: bool = False) -> Engine:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    return create_database_engine(
        database_url,
        allow_sqlite_for_tests=allow_sqlite_for_tests,
        echo=os.environ.get("SQL_ECHO", "false").lower() == "true",
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def transactional_session(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        with session.begin():
            yield session
    finally:
        session.close()
