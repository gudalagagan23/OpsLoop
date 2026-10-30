from typing import Annotated, Any

from fastapi import Depends, FastAPI, Response

from opsloop.core.db import DatabaseStatus, check_database

app = FastAPI(title="OpsLoop API")


def get_db_status() -> DatabaseStatus:
    return check_database()


@app.get("/health")
def health(
    response: Response, db: Annotated[DatabaseStatus, Depends(get_db_status)]
) -> dict[str, Any]:
    ok = db.reachable and db.pgvector
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "degraded", "database": db.reachable, "pgvector": db.pgvector}
