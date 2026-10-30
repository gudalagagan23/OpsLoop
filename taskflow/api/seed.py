"""Create the TaskFlow schema and seed deterministic simulated data."""

import hashlib

from sqlalchemy import Engine, text

from taskflow.api.models import SCHEMA, TASKS_PROJECT_STATUS_INDEX, Base
from taskflow.config import TaskflowSettings

# Every seeded (simulated) user shares this password. Used by the traffic generator.
SIM_PASSWORD = "taskflow-sim-password"  # noqa: S105 - simulated users only
_SALT = b"taskflow-sim"


def hash_password(password: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), _SALT, 10_000).hex()


def ensure_task_index(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {TASKS_PROJECT_STATUS_INDEX} "
                f"ON {SCHEMA}.tasks (project_id, status)"
            )
        )


def init_db(engine: Engine, settings: TaskflowSettings) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    Base.metadata.create_all(engine)
    ensure_task_index(engine)  # a restart always clears planted fault 3
    with engine.begin() as conn:
        if conn.execute(text(f"SELECT EXISTS (SELECT 1 FROM {SCHEMA}.tenants)")).scalar_one():
            return
        conn.execute(text("SELECT setseed(0.42)"))
        conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.tenants (name, plan) "
                "SELECT 'tenant-' || g, CASE WHEN g <= 2 THEN 'enterprise' "
                "WHEN g <= 8 THEN 'pro' ELSE 'free' END FROM generate_series(1, :t) g"
            ),
            {"t": settings.seed_tenants},
        )
        conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.users (tenant_id, email, name, password_hash) "
                "SELECT t.id, 'user' || u || '@tenant' || t.id || '.sim', 'User ' || u, :h "
                f"FROM {SCHEMA}.tenants t, generate_series(1, 3) u ORDER BY t.id, u"
            ),
            {"h": hash_password(SIM_PASSWORD)},
        )
        conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.projects (tenant_id, name) "
                f"SELECT t.id, 'Project ' || p FROM {SCHEMA}.tenants t, generate_series(1, :p) p "
                "ORDER BY t.id, p"
            ),
            {"p": settings.seed_projects_per_tenant},
        )
        conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.tasks "
                "(project_id, tenant_id, title, description, status, assignee_id, created_at) "
                "SELECT pr.id, pr.tenant_id, 'Task ' || g, repeat('simulated task text ', 6), "
                "(ARRAY['todo','in_progress','review','done'])[1 + floor(random() * 4)::int], "
                f"(SELECT u.id FROM {SCHEMA}.users u WHERE u.tenant_id = pr.tenant_id "
                "ORDER BY u.id OFFSET floor(random() * 3)::int LIMIT 1), "
                "now() - random() * interval '90 days' "
                f"FROM {SCHEMA}.projects pr, generate_series(1, :n) g"
            ),
            {"n": settings.seed_tasks_per_project},
        )
        conn.execute(
            text(
                f"INSERT INTO {SCHEMA}.comments (task_id, user_id, body) "
                f"SELECT id, assignee_id, 'Simulated comment' FROM {SCHEMA}.tasks "
                "WHERE assignee_id IS NOT NULL AND random() < 0.3"
            )
        )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"ANALYZE {SCHEMA}.tasks"))
        conn.execute(text(f"ANALYZE {SCHEMA}.comments"))
