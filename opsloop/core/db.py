from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from opsloop.core.config import get_settings


@dataclass(frozen=True)
class DatabaseStatus:
    reachable: bool
    pgvector: bool


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def check_database() -> DatabaseStatus:
    """Report whether Postgres answers and the pgvector extension is installed."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            has_vector = conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            ).scalar_one()
    except SQLAlchemyError:
        return DatabaseStatus(reachable=False, pgvector=False)
    return DatabaseStatus(reachable=True, pgvector=bool(has_vector))
