"""Shared fixtures. Integration tests use a throwaway database `opsloop_test` on the same server."""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from opsloop.core.config import get_settings

TEST_DB = "opsloop_test"


def _test_url() -> str:
    return make_url(get_settings().database_url).set(database=TEST_DB).render_as_string(False)


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    admin_url = make_url(get_settings().database_url).set(database="postgres")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    except OperationalError as exc:
        if os.environ.get("OPSLOOP_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Postgres not reachable ({exc.__class__.__name__}); run docker compose up")
    finally:
        admin.dispose()

    url = _test_url()
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db(db_engine: Engine) -> Iterator[Session]:
    session = sessionmaker(bind=db_engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
