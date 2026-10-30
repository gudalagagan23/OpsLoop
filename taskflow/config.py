from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class TaskflowSettings(BaseSettings):
    """Simulated SaaS app settings, from TASKFLOW_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="TASKFLOW_", extra="ignore")

    database_url: str = "postgresql+psycopg://opsloop:opsloop@localhost:55432/opsloop"
    # Simulated app only. Override TASKFLOW_JWT_SECRET outside local development.
    jwt_secret: str = "sim-dev-secret-not-for-production"  # noqa: S105
    jwt_ttl_s: int = 3600
    pool_size: int = 10
    pool_timeout_s: float = 3.0
    version: str = "1.0.0"
    git_sha: str = "dev"
    seed_tenants: int = 20
    seed_projects_per_tenant: int = 5
    seed_tasks_per_project: int = 4000
    slow_request_ms: float = 500.0
    info_log_sample: float = 0.02
    log_to_db: bool = True


@lru_cache
def get_settings() -> TaskflowSettings:
    return TaskflowSettings()
