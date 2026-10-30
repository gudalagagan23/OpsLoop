import os

import pytest

from opsloop.core.db import check_database

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.environ.get("OPSLOOP_DATABASE_URL"),
    reason="OPSLOOP_DATABASE_URL not set; start Postgres with docker compose",
)
def test_database_reachable_with_pgvector() -> None:
    status = check_database()
    assert status.reachable
    assert status.pgvector
