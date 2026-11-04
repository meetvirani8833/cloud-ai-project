"""Database engine, session factory and one-time bootstrap (tables + TimescaleDB hypertables)."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool

        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Additive schema changes for databases created by an earlier version (create_all never alters).
COLUMN_MIGRATIONS = [
    "ALTER TABLE services ADD COLUMN IF NOT EXISTS decision_engine VARCHAR(12) NOT NULL DEFAULT 'forecast'",
]

# Time-series tables turned into hypertables when TimescaleDB is available.
HYPERTABLES = {"usage_metrics": "time", "forecast_points": "target_time"}


def init_db(bind=None) -> None:
    from app import models  # noqa: F401  (register tables)

    bind = bind or engine
    Base.metadata.create_all(bind)
    if bind.dialect.name != "postgresql":
        return
    with bind.begin() as conn:
        for stmt in COLUMN_MIGRATIONS:
            conn.execute(text(stmt))
    with bind.begin() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        except Exception:  # plain Postgres (e.g. a free host without the extension) still works
            log.warning("TimescaleDB extension unavailable; using plain PostgreSQL tables")
            return
    for table, col in HYPERTABLES.items():
        with bind.begin() as conn:
            conn.execute(text(
                f"SELECT create_hypertable('{table}', '{col}', if_not_exists => TRUE, migrate_data => TRUE)"
            ))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
